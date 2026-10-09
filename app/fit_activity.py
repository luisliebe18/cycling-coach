"""FIT-Datei -> ActivityData (einheitliches Importformat)."""
from __future__ import annotations

from .fit_parser import CYCLING_SPORT_CODES, FitFile, fit_timestamp_to_dt
from .text_parsers import ActivityData


def parse_fit(data: bytes) -> list[ActivityData]:
    ff = FitFile(data)
    sessions = ff.get('session')
    records = ff.get('record')
    laps = ff.get('lap')
    file_id = ff.first('file_id') or {}
    devices = [d for d in ff.get('device_info') if isinstance(d.get('manufacturer'), int)]
    # device_info-Felder sind ggf. generisch benannt (f2/f3/f4)
    def dev_desc(d: dict) -> str | None:
        mfg = d.get('manufacturer', d.get('f2'))
        prod = d.get('product', d.get('f3'))
        name = d.get('descriptor') or d.get('f10')
        if isinstance(name, str) and name.strip():
            return name.strip()
        if mfg is not None:
            return f"{ff.manufacturer_name(mfg)}" + (f" Produkt {prod}" if prod else "")
        return None

    out: list[ActivityData] = []
    rec_idx = 0
    for s_i, sess in enumerate(sessions):
        a = ActivityData()
        start = fit_timestamp_to_dt(sess.get('start_time'))
        a.start_time = start
        sport_code = sess.get('sport')
        a.sport = ff.sport_name(sport_code)
        elapsed = sess.get('total_elapsed_time')
        timer = sess.get('total_timer_time')
        a.duration_s = (elapsed / 1000.0) if isinstance(elapsed, (int, float)) else None
        a.moving_time_s = (timer / 1000.0) if isinstance(timer, (int, float)) else None
        dist = sess.get('total_distance')
        a.distance_m = (dist / 100.0) if isinstance(dist, (int, float)) else None
        asc = sess.get('total_ascent')
        a.elevation_gain_m = float(asc) if isinstance(asc, (int, float)) else None
        for k_attr, keys in (('avg_hr', ('avg_heart_rate',)),
                             ('max_hr', ('max_heart_rate',)),
                             ('avg_cadence', ('avg_cadence',)),
                             ('avg_power', ('avg_power',)),
                             ('max_power', ('max_power',))):
            for key in keys:
                v = sess.get(key)
                if isinstance(v, (int, float)):
                    setattr(a, k_attr, float(v))
                    break
        esp = sess.get('avg_enhanced_speed')
        if isinstance(esp, (int, float)):
            a.avg_speed = esp / 1000.0
        elif isinstance(sess.get('speed'), (int, float)):
            a.avg_speed = sess['speed'] / 100.0
        np_ = sess.get('normalized_power')
        if isinstance(np_, (int, float)):
            a.fit_np = float(np_)
        # zugeordnete Records/Laps (über num_laps-Split bzw. Zeitfenster)
        lap_start = fit_timestamp_to_dt(sess.get('start_time'))
        lap_end = fit_timestamp_to_dt(sess.get('timestamp'))
        seg_records = [r for r in records
                       if r.get('timestamp') is not None
                       and (lap_start is None or r['timestamp'] >= (sess.get('start_time') or 0))
                       and (lap_end is None or r['timestamp'] <= (sess.get('timestamp') or 0x7FFFFFFF))]
        base_ts = seg_records[0]['timestamp'] if seg_records else (sess.get('start_time') or 0)
        temps_seen = False
        for r in seg_records:
            t = (r['timestamp'] - base_ts) if r.get('timestamp') is not None else len(a.t)
            lat, lon = ff.scaled_latlon(r)
            alt = ff.altitude_m(r)
            spd = ff.speed_mps(r)
            pw = r.get('power')
            hr = r.get('heart_rate')
            cad = r.get('cadence')
            tmp = r.get('temperature')
            a.t.append(float(t))
            dtv = fit_timestamp_to_dt(r.get('timestamp'))
            a.iso.append(dtv.isoformat() if dtv else '')
            a.power.append(float(pw) if isinstance(pw, (int, float)) else None)
            a.hr.append(int(hr) if isinstance(hr, (int, float)) else None)
            a.cadence.append(float(cad) if isinstance(cad, (int, float)) else None)
            a.speed.append(spd)
            a.lat.append(lat)
            a.lon.append(lon)
            a.alt.append(alt)
            a.temp.append(float(tmp) if isinstance(tmp, (int, float)) else None)
            if tmp is not None:
                temps_seen = True
        if not temps_seen:
            a.temp = []
        # Arbeit: akkumulierte Leistung bevorzugen, sonst Summe * Samplingzeit
        acc_pow = None
        for r in reversed(seg_records):
            if isinstance(r.get('accumulated_power'), (int, float)):
                acc_pow = r['accumulated_power']
                break
        if acc_pow is not None:
            a.work_kj = acc_pow / 1000.0
        else:
            vals = [p for p in a.power if p is not None]
            if vals and len(a.t) > 1:
                avg_dt = (a.t[-1] - a.t[0]) / (len(a.t) - 1)
                a.work_kj = sum(vals) * avg_dt / 1000.0
        # Runden dieses Sessions
        my_laps = [l for l in laps
                   if l.get('start_time') is not None and start is not None
                   and l['start_time'] >= (sess.get('start_time') or 0) - 1
                   and (l.get('timestamp') or 0) <= (sess.get('timestamp') or 0x7FFFFFFF) + 1]
        for i, l in enumerate(my_laps):
            lt0 = fit_timestamp_to_dt(l.get('start_time'))
            lt1 = fit_timestamp_to_dt(l.get('timestamp'))
            e0 = (lt0 - start).total_seconds() if (lt0 and start) else 0
            e1 = (lt1 - start).total_seconds() if (lt1 and start) else None
            def lv(*keys):
                for k in keys:
                    v = l.get(k)
                    if isinstance(v, (int, float)):
                        return float(v)
                return None
            a.laps.append({
                'lap_no': i + 1,
                'start_epoch': e0, 'end_epoch': e1,
                'distance_m': lv('total_distance') ,
                'time_s': lv('total_elapsed_time'),
                'avg_power': lv('avg_power'), 'max_power': lv('max_power'),
                'avg_hr': lv('avg_heart_rate'), 'max_hr': lv('max_heart_rate'),
                'avg_cadence': lv('avg_cadence'),
                'elevation_gain_m': lv('total_ascent'),
            })
            # Distanz der Lap-Felder ist in cm? Nein: total_distance in cm bei Session;
            # normalisieren:
        for lp in a.laps:
            if lp['distance_m'] and lp['distance_m'] > 500000:
                lp['distance_m'] /= 100.0
        dev_names = [dev_desc(d) for d in devices]
        dev_names = [d for d in dev_names if d]
        if dev_names:
            a.device_info = "; ".join(dict.fromkeys(dev_names))
        elif file_id.get('manufacturer') is not None:
            a.device_info = ff.manufacturer_name(file_id['manufacturer'])
        uid = None
        if isinstance(file_id.get('serial_number'), int) and start is not None:
            uid = f"{file_id['serial_number']}-{int(start.timestamp())}"
        a.uid = uid
        a.name = f"Fahrt {(start.strftime('%Y-%m-%d %H:%M') if start else '')}".strip()
        if sport_code in CYCLING_SPORT_CODES:
            a.activity_type = 'Ride'
        else:
            a.activity_type = (a.sport or 'Activity').title().replace('_', ' ')
        out.append(a)
    if not out and records:
        # Datei ohne Session (selten) -> alles als eine Aktivität
        a = ActivityData()
        first_ts = next((r['timestamp'] for r in records if r.get('timestamp')), None)
        a.start_time = fit_timestamp_to_dt(first_ts)
        for r in records:
            t = (r['timestamp'] - first_ts) if r.get('timestamp') is not None else len(a.t)
            lat, lon = ff.scaled_latlon(r)
            a.t.append(float(t))
            dtv = fit_timestamp_to_dt(r.get('timestamp'))
            a.iso.append(dtv.isoformat() if dtv else '')
            pw = r.get('power'); hr = r.get('heart_rate'); cad = r.get('cadence')
            a.power.append(float(pw) if isinstance(pw, (int, float)) else None)
            a.hr.append(int(hr) if isinstance(hr, (int, float)) else None)
            a.cadence.append(float(cad) if isinstance(cad, (int, float)) else None)
            a.speed.append(ff.speed_mps(r))
            a.lat.append(lat); a.lon.append(lon); a.alt.append(ff.altitude_m(r))
            a.temp.append(None)
        vals = [p for p in a.power if p is not None]
        a.avg_power = sum(vals) / len(vals) if vals else None
        a.max_power = max(vals) if vals else None
        hs = [h for h in a.hr if h is not None]
        a.avg_hr = sum(hs) / len(hs) if hs else None
        a.max_hr = max(hs) if hs else None
        cds = [c for c in a.cadence if c is not None]
        a.avg_cadence = sum(cds) / len(cds) if cds else None
        a.duration_s = a.t[-1] if a.t else None
        a.name = f"Fahrt {a.start_time.strftime('%Y-%m-%d %H:%M') if a.start_time else ''}".strip()
        a.activity_type = 'Ride'
        out.append(a)
    return out
