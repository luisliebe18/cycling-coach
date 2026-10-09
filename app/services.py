"""Settings-Historie (FTP/Gewicht mit Gültigkeitsdatum) + Auth + Import-Orchestrierung."""
from __future__ import annotations

import gzip
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone

from . import config, db


def _parse_tcx_bytes(data: bytes):
    from .text_parsers import parse_tcx
    return parse_tcx(data)


def _parse_gpx_bytes(data: bytes):
    from .text_parsers import parse_gpx
    return parse_gpx(data)


def _parse_fit_bytes(data: bytes):
    from .fit_activity import parse_fit
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return parse_fit(data)


# ---------------------------------------------------------------------------
#  Einstellungen / Historie
# ---------------------------------------------------------------------------

def get_setting(con: sqlite3.Connection, key: str, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row['value'] if row else default


def set_setting(con: sqlite3.Connection, key: str, value) -> None:
    con.execute("INSERT INTO settings(key,value) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
    con.commit()


def add_history(con: sqlite3.Connection, key: str, value: float,
                effective_from: str, source: str = 'manual', note: str | None = None) -> None:
    """Eintrag mit Gültigkeitsdatum; überlappende Einträge werden ersetzt."""
    con.execute(
        "DELETE FROM settings_history WHERE key=? AND effective_from>=?",
        (key, effective_from))
    con.execute(
        "INSERT INTO settings_history(key,value,effective_from,source,note)"
        " VALUES(?,?,?,?,?)", (key, float(value), effective_from, source, note))
    con.commit()


def history_value(con: sqlite3.Connection, key: str, when_iso: str | None = None,
                  default: float | None = None) -> tuple[float | None, str]:
    """Wert, der zum Zeitpunkt when_iso galt. Returns (wert, quelle)."""
    if when_iso:
        row = con.execute(
            "SELECT value, source FROM settings_history WHERE key=? AND effective_from<=?"
            " ORDER BY effective_from DESC LIMIT 1", (key, when_iso)).fetchone()
    else:
        row = con.execute(
            "SELECT value, source FROM settings_history WHERE key=?"
            " ORDER BY effective_from DESC LIMIT 1", (key,)).fetchone()
    if row:
        return float(row['value']), row['source'] or 'manual'
    # Fallback: aktuelle Einstellung ohne Historieinträge
    v = get_setting(con, key)
    if v is not None:
        try:
            return float(v), 'current'
        except ValueError:
            pass
    return default, 'none'


def current_ftp(con: sqlite3.Connection) -> tuple[float | None, str]:
    return history_value(con, 'ftp')


def current_weight(con: sqlite3.Connection) -> tuple[float | None, str]:
    return history_value(con, 'weight')


def ftp_for_activity_time(con: sqlite3.Connection, start_iso: str) -> tuple[float | None, str]:
    """Zum Aktivitätsstart gültiger FTP-Wert (Historie), sonst aktueller."""
    return history_value(con, 'ftp', start_iso)


def weight_for_time(con: sqlite3.Connection, when_iso: str | None) -> tuple[float | None, str]:
    return history_value(con, 'weight', when_iso)


def full_history(con: sqlite3.Connection, key: str) -> list[dict]:
    rows = con.execute(
        "SELECT value, effective_from, source, note FROM settings_history"
        " WHERE key=? ORDER BY effective_from", (key,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
#  Auth (single-user, scrypt-Passwort-Hash)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(stored: str, password: str) -> bool:
    try:
        algo, salt_hex, hash_hex = stored.split('$')
        if algo != 'scrypt':
            return False
        dk = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt_hex),
                            n=2 ** 14, r=8, p=1, dklen=len(bytes.fromhex(hash_hex)))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def create_user(con: sqlite3.Connection, username: str, password: str) -> bool:
    if len(password) < 8:
        return False
    existing = con.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if existing:
        return False
    con.execute("CREATE TABLE IF NOT EXISTS users ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " username TEXT UNIQUE NOT NULL,"
                " pw_hash TEXT NOT NULL,"
                " created_at TEXT DEFAULT (datetime('now')))")
    con.execute("INSERT INTO users(username,pw_hash) VALUES(?,?)",
                (username, hash_password(password)))
    con.commit()
    return True


def check_login(con: sqlite3.Connection, username: str, password: str) -> bool:
    row = con.execute("SELECT pw_hash FROM users WHERE username=?", (username,)).fetchone()
    return bool(row) and verify_password(row['pw_hash'], password)


def user_exists(con: sqlite3.Connection) -> bool:
    try:
        return con.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def make_session_token(username: str) -> str:
    if not config.SECRET_KEY:
        raise RuntimeError("CYCLING_SECRET_KEY nicht gesetzt")
    payload = f"{username}|{secrets.token_hex(8)}|{int(datetime.now(timezone.utc).timestamp())}"
    sig = hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}|{sig}"


def verify_session_token(token: str) -> str | None:
    if not token or not config.SECRET_KEY:
        return None
    parts = token.rsplit('|', 1)
    if len(parts) != 2:
        return None
    payload, sig = parts
    expect = hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        ts = int(payload.split('|')[2])
    except (ValueError, IndexError):
        return None
    age_days = (datetime.now(timezone.utc).timestamp() - ts) / 86400.0
    if age_days > config.SESSION_MAX_AGE_S / 86400.0:
        return None
    return payload.split('|')[0]


# ---------------------------------------------------------------------------
#  Datei-/ZIP-Sicherheit
# ---------------------------------------------------------------------------

SUPPORTED_EXT = {'.fit', '.tcx', '.gpx'}

FILE_MAGIC = {
    '.fit': None,   # FIT wird über Header geprüft
    '.gz': b'\x1f\x8b',
    '.tcx': b'<',    # XML beginnt mit '<' (BOM toleriert)
    '.gpx': b'<',
}


def sniff_kind(filename: str, head: bytes) -> str | None:
    """Prüft Erweiterung UND Inhalt. Gibt 'fit'|'fit.gz'|'tcx'|'gpx'|None zurück."""
    low = filename.lower()
    stripped = head.lstrip(b'\xef\xbb\xbf \t\r\n')
    if low.endswith('.fit.gz'):
        return 'fit.gz' if head[:2] == b'\x1f\x8b' else None
    if low.endswith('.fit'):
        if len(head) >= 12 and head[0] in (12, 14) and chr(head[7] if len(head) > 7 else ' ') == '.' \
                and (head[4] & 0x08):
            return 'fit'
        if head[:2] == b'\x1f\x8b':
            return 'fit.gz'
        return None
    if low.endswith('.tcx'):
        return 'tcx' if b'TrainingCenterDatabase' in head[:4096] else ('tcx' if stripped[:1] == b'<' else None)
    if low.endswith('.gpx'):
        if b'gpx' in head[:4096].lower():
            return 'gpx'
        return 'gpx' if stripped[:1] == b'<' else None
    if low.endswith('.zip'):
        return 'zip' if head[:2] == b'PK' else None
    return None


def safe_zip_entries(zf) -> list:
    """ZIP-Bomben- und Path-Traversal-Schutz: prüft Namen und deklarierte Größen."""
    import zipfile
    infos = zf.infolist()
    if len(infos) > config.MAX_ZIP_ENTRIES:
        raise ValueError(f"ZIP enthält zu viele Einträge ({len(infos)})")
    total = sum(i.file_size for i in infos)
    if total > config.MAX_ZIP_UNCOMPRESSED:
        raise ValueError("ZIP entpackt zu groß (ZIP-Bombe wahrscheinlich)")
    out = []
    for i in infos:
        name = i.filename
        if name.startswith('/') or '..' in name.split('/') or '\x00' in name:
            raise ValueError(f"Gefährlicher ZIP-Eintrag: {name}")
        if i.is_dir():
            continue
        if i.file_size > config.MAX_ZIP_ENTRY_SIZE:
            raise ValueError(f"ZIP-Eintrag zu groß: {name}")
        out.append(i)
    return out


# ---------------------------------------------------------------------------
#  Import
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _log_import(con, filename, status, message, activity_id=None):
    con.execute("INSERT INTO import_log(filename,status,message,activity_id) VALUES(?,?,?,?)",
                (filename, status, message, activity_id))
    con.commit()


def detect_indoor(a) -> int | None:
    """Indoor erkennbar: keine GPS-Punkte, aber Watt/Kadenz über längere Zeit."""
    try:
        if a.lat and any(x is not None for x in a.lat):
            return 0
        if a.power and any(p is not None and p > 0 for p in a.power):
            return 1
    except Exception:
        pass
    return None


def store_activity(con: sqlite3.Connection, a, *, filename: str | None,
                   file_sha: str | None, source: str = 'import',
                   reprocess_of: int | None = None) -> dict:
    """ActivityData -> activities + series + laps. Berechnet NP/IF/TSS/MMP/PB.

    Duplikaterkennung über uid ODER (start_time ±2s & duration ±5s & distance ±1%).
    """
    import json

    from . import calc, mmp_store
    from .intervals import detect_work_intervals

    if a.start_time is None:
        raise ValueError("Aktivität ohne Startzeit kann nicht gespeichert werden")
    start_iso = a.start_time.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    dup = find_duplicate(con, getattr(a, 'uid', None), start_iso,
                         a.duration_s, a.distance_m, exclude_id=reprocess_of)
    if dup:
        return {'status': 'duplicate', 'activity_id': dup['id'],
                'message': f"Duplikat von Aktivität {dup['id']} ({dup['name']})"}

    ftp, ftp_src = ftp_for_activity_time(con, start_iso)
    weight, _ = weight_for_time(con, start_iso)

    np_val = calc.normalized_power(a.t, a.power)
    if np_val is None and hasattr(a, 'fit_np'):
        np_val = a.fit_np  # vom Gerät berechnete NP verwenden (dokumentiert)
    ifv = calc.intensity_factor(np_val, ftp)
    dur = a.moving_time_s or a.duration_s
    tss_val = calc.tss(np_val, dur, ftp)
    work = a.work_kj
    if work is None:
        work = calc.work_kj(a.t, a.power)

    has = lambda seq: any(v is not None for v in seq) if seq else False
    indoor = detect_indoor(a)

    if reprocess_of:
        act_id = reprocess_of
        con.execute("DELETE FROM series WHERE activity_id=?", (act_id,))
        con.execute("DELETE FROM laps WHERE activity_id=?", (act_id,))
        con.execute("DELETE FROM segments WHERE activity_id=?", (act_id,))
        con.execute("DELETE FROM mmp_entries WHERE activity_id=?", (act_id,))
        cur = con.execute("""UPDATE activities SET uid=?, source=?, filename=?, file_sha256=?,
                name=?, activity_type=?, sport=?, start_time=?, duration_s=?, moving_time_s=?,
                distance_m=?, elevation_gain_m=?, avg_speed=?, avg_power=?, np_power=?,
                max_power=?, work_kj=?, avg_hr=?, max_hr=?, avg_cadence=?,
                hr_data=?, power_data=?, cadence_data=?, has_gps=?, indoor=?,
                device_info=?, if_value=?, tss=?, updated_at=datetime('now') WHERE id=?""",
            (getattr(a, 'uid', None), source, filename, file_sha, a.name,
             getattr(a, 'activity_type', 'Ride'), a.sport, start_iso, a.duration_s,
             a.moving_time_s, a.distance_m, a.elevation_gain_m, a.avg_speed,
             a.avg_power, np_val, a.max_power, work, a.avg_hr, a.max_hr,
             a.avg_cadence, int(has(a.hr)), int(has(a.power)), int(has(a.cadence)),
             int(has(a.lat)), indoor, a.device_info, ifv, tss_val, act_id))
    else:
        cur = con.execute("""INSERT INTO activities(uid, source, filename, file_sha256, name,
                activity_type, sport, start_time, duration_s, moving_time_s, distance_m,
                elevation_gain_m, avg_speed, avg_power, np_power, max_power, work_kj,
                avg_hr, max_hr, avg_cadence, hr_data, power_data, cadence_data, has_gps,
                indoor, device_info, if_value, tss)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (getattr(a, 'uid', None), source, filename, file_sha, a.name,
             getattr(a, 'activity_type', 'Ride'), a.sport, start_iso, a.duration_s,
             a.moving_time_s, a.distance_m, a.elevation_gain_m, a.avg_speed,
             a.avg_power, np_val, a.max_power, work, a.avg_hr, a.max_hr,
             a.avg_cadence, int(has(a.hr)), int(has(a.power)), int(has(a.cadence)),
             int(has(a.lat)), indoor, a.device_info, ifv, tss_val))
        act_id = cur.lastrowid

    def col(seq):
        if not seq:
            return None
        if not any(v is not None for v in seq):
            return None
        return json.dumps(seq)

    con.execute("INSERT INTO series(activity_id,n,epoch,time,power,hr,cadence,speed,lat,lon,alt,temp)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (act_id, len(a.t), json.dumps(a.t), json.dumps(a.iso),
                 col(a.power), col(a.hr), col(a.cadence), col(a.speed),
                 col(a.lat), col(a.lon), col(a.alt), col(a.temp)))

    for lap in a.laps:
        con.execute("""INSERT INTO laps(activity_id,lap_no,start_epoch,end_epoch,distance_m,
                time_s,avg_power,max_power,avg_hr,max_hr,avg_cadence,avg_speed,elevation_gain_m)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (act_id, lap.get('lap_no'), lap.get('start_epoch'), lap.get('end_epoch'),
             lap.get('distance_m'), lap.get('time_s'), lap.get('avg_power'),
             lap.get('max_power'), lap.get('avg_hr'), lap.get('max_hr'),
             lap.get('avg_cadence'), None, lap.get('elevation_gain_m')))

    # Belastungsabschnitte automatisch erkennen
    segs = detect_work_intervals(a.t, a.power, ftp)
    for s in segs:
        con.execute("INSERT INTO segments(activity_id,kind,start_epoch,end_epoch,avg_power,max_power,avg_hr,length_s)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (act_id, s['kind'], s['start'], s['end'], s['avg_power'],
                     s['max_power'], s.get('avg_hr'), s['end'] - s['start']))

    # MMP + PB
    mmp_store.update_mmp_and_pb(con, act_id, a.t, a.power, start_iso, weight)

    con.commit()
    return {'status': 'ok', 'activity_id': act_id, 'name': a.name,
            'start': start_iso, 'np': np_val, 'if': ifv, 'tss': tss_val,
            'ftp_source': ftp_src}


def find_duplicate(con, uid, start_iso, duration_s, distance_m, exclude_id=None):
    if uid:
        row = con.execute("SELECT id,name FROM activities WHERE uid=? AND (? IS NULL OR id!=?)",
                          (uid, exclude_id, exclude_id)).fetchone()
        if row:
            return row
    # zeitbasiert: gleicher Start (±2s) plus ähnliche Dauer/Distanz
    row = con.execute(
        """SELECT id,name,start_time,duration_s,distance_m FROM activities
           WHERE abs(strftime('%s',start_time) - strftime('%s',?)) <= 2
             AND (? IS NULL OR id!=?)""", (start_iso, exclude_id, exclude_id)).fetchone()
    if row:
        d_ok = (duration_s is None and row['duration_s'] is None) or \
               (duration_s and row['duration_s'] and abs(duration_s - row['duration_s']) <= 5)
        m_ok = (distance_m is None) or (row['distance_m'] is None) or \
               (distance_m and row['distance_m'] and
                abs(distance_m - row['distance_m']) <= max(50.0, row['distance_m'] * 0.01))
        if d_ok and m_ok:
            return row
    return None


PARSERS = {'fit': _parse_fit_bytes, 'fit.gz': _parse_fit_bytes,
           'tcx': _parse_tcx_bytes, 'gpx': _parse_gpx_bytes}


def import_file_bytes(con, filename: str, data: bytes, *, save_copy: bool = True) -> list[dict]:
    """Import einer einzelnen Datei (fit/tcx/gpx, fit.gz). Gibt Berichte zurück."""
    kind = sniff_kind(filename, data[:4096])
    results = []
    if kind is None:
        _log_import(con, filename, 'error', 'Dateityp/Inhalt passt nicht zusammen')
        return [{'status': 'error', 'filename': filename,
                 'message': 'Unbekanntes oder beschädigtes Dateiformat'}]
    try:
        acts = PARSERS[kind](data)
    except Exception as e:
        _log_import(con, filename, 'error', f"Parserfehler: {e}")
        return [{'status': 'error', 'filename': filename,
                 'message': f"Parsefehler: {e}"}]

    saved_name = None
    if save_copy:
        config.ensure_dirs()
        safe = "".join(c for c in filename if c.isalnum() or c in '._-')[-96:] or 'file'
        saved_name = f"{secrets.token_hex(4)}_{safe}"
        try:
            (config.UPLOAD_DIR / saved_name).write_bytes(data)
        except OSError:
            saved_name = None

    fsha = sha256_bytes(data)
    for a in acts:
        try:
            res = store_activity(con, a, filename=saved_name, file_sha=fsha)
            res['filename'] = filename
            _log_import(con, filename, res['status'], res.get('message', res.get('name', '')),
                        res.get('activity_id'))
            results.append(res)
        except Exception as e:
            _log_import(con, filename, 'error', f"Speicherfehler: {e}")
            results.append({'status': 'error', 'filename': filename,
                            'message': f"Speicherfehler: {e}"})
    return results


def import_zip(con, filename: str, data: bytes) -> list[dict]:
    import io
    import zipfile
    results = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        _log_import(con, filename, 'error', 'Beschädigtes ZIP')
        return [{'status': 'error', 'filename': filename, 'message': 'Beschädigtes ZIP-Archiv'}]
    try:
        entries = safe_zip_entries(zf)
    except ValueError as e:
        _log_import(con, filename, 'error', str(e))
        return [{'status': 'error', 'filename': filename, 'message': str(e)}]
    for info in entries:
        base = os.path.basename(info.filename.lower())
        if not any(base.endswith(ext) for ext in SUPPORTED_EXT) and \
           not base.endswith('.fit.gz'):
            continue
        if base.startswith('._') or base.startswith('.'):
            continue  # macOS-Ressourcenforks
        with zf.open(info) as fh:
            sub = fh.read(config.MAX_FILE_BYTES + 1)
        if len(sub) > config.MAX_FILE_BYTES:
            results.append({'status': 'error', 'filename': info.filename,
                            'message': 'Datei im ZIP zu groß'})
            continue
        results.extend(import_file_bytes(con, info.filename, sub))
    return results


def import_upload(con, filename: str, data: bytes) -> list[dict]:
    low = filename.lower()
    if len(data) > config.MAX_FILE_BYTES and not low.endswith('.zip'):
        return [{'status': 'error', 'filename': filename,
                 'message': f"Datei größer als {config.MAX_FILE_BYTES // 1048576} MB Limit"}]
    if low.endswith('.zip'):
        return import_zip(con, filename, data)
    return import_file_bytes(con, filename, data)


def reprocess_activity(con, activity_id: int) -> dict:
    """Aktivität aus erhaltener Originaldatei erneut parsen (Parser-Update)."""
    row = con.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
    if not row:
        return {'status': 'error', 'message': 'Aktivität nicht gefunden'}
    fname = row['filename']
    if not fname:
        return {'status': 'error', 'message': 'Keine Originaldatei referenziert'}
    path = config.UPLOAD_DIR / os.path.basename(fname)
    if not path.exists():
        return {'status': 'error', 'message': 'Originaldatei nicht mehr vorhanden'}
    data = path.read_bytes()
    kind = sniff_kind(fname, data[:4096])
    try:
        if kind not in PARSERS:
            return {'status': 'error', 'message': 'Format nicht erkennbar'}
        acts = PARSERS[kind](data)
    except Exception as e:
        return {'status': 'error', 'message': f'Parsefehler: {e}'}
    target_uid = row['uid']
    start_iso = row['start_time']
    for a in acts:
        a_start = a.start_time.strftime('%Y-%m-%dT%H:%M:%SZ') if a.start_time else ''
        if (getattr(a, 'uid', None) and a.uid == target_uid) or a_start == start_iso:
            res = store_activity(con, a, filename=row['filename'],
                                 file_sha=row['file_sha256'], reprocess_of=activity_id)
            res['reprocessed'] = True
            _log_import(con, fname, 'ok', f"Reprocessing Aktivität {activity_id}: {res.get('message','')}")
            return res
    return {'status': 'error', 'message': 'Passende Aktivität in Datei nicht gefunden'}
