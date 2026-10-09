"""API-Router: Auth, Dashboard, Aktivitäten, Series, Power Curve, PBs,
Belastung, Coach, Plan, Bibliothek, Einstellungen, Import."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import calc, coach, config, db, mmp_store, services

router = APIRouter()


def get_db():
    con = db.get_conn()
    try:
        yield con
    finally:
        con.close()


# ---------------- Session/Auth ----------------------------------------------

def _session_token(request: Request) -> str:
    """Session-Token aus Cookie oder Authorization-Header lesen."""
    return request.cookies.get(config.SESSION_COOKIE) or \
        (request.headers.get('authorization') or '').replace('Bearer ', '')


def require_auth(request: Request, con: sqlite3.Connection = Depends(get_db)) -> str:
    token = _session_token(request)
    user = services.verify_session_token(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail='Nicht angemeldet')
    return user


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post('/auth/login')
def login(body: LoginBody, response: JSONResponse, con: sqlite3.Connection = Depends(get_db)):
    if not services.user_exists(con):
        # Bootstrap: erster Aufruf legt Konto an — Passwort nie im Quellcode.
        if len(body.password) < 8:
            raise HTTPException(400, 'Erstanmeldung: Passwort muss >= 8 Zeichen haben')
        if not services.create_user(con, body.username, body.password):
            raise HTTPException(500, 'Konto konnte nicht angelegt werden')
        bootstrapped = True
    else:
        bootstrapped = False
        if not services.check_login(con, body.username, body.password):
            raise HTTPException(401, 'Anmeldung fehlgeschlagen')
    token = services.make_session_token(body.username)
    response.set_cookie(config.SESSION_COOKIE, token, httponly=True, samesite='lax',
                        max_age=config.SESSION_MAX_AGE_S, secure=config.COOKIE_SECURE)
    return {'ok': True, 'user': body.username, 'bootstrapped': bootstrapped}


@router.get('/auth/status')
def auth_status(request: Request, con: sqlite3.Connection = Depends(get_db)):
    token = _session_token(request)
    user = services.verify_session_token(token) if token else None
    return {'authenticated': bool(user), 'user': user,
            'needs_setup': not services.user_exists(con)}


@router.post('/auth/logout')
def logout(response: JSONResponse):
    response.delete_cookie(config.SESSION_COOKIE)
    return {'ok': True}


# ---------------- Settings ---------------------------------------------------

class SettingBody(BaseModel):
    ftp: float | None = Field(None, ge=30, le=600)
    weight: float | None = Field(None, ge=25, le=200)
    goal: str | None = None
    max_duration_min: int | None = Field(None, ge=15, le=600)
    available_days: list[int] | None = Field(None, min_length=7, max_length=7)
    units: str | None = None
    timezone: str | None = None
    effective_from: str | None = None   # YYYY-MM-DD für FTP/Gewicht-Historie
    source: str = 'manual'
    power_zones: list[dict] | None = None
    events: list[dict] | None = None          # [{'name': str, 'date': 'YYYY-MM-DD'}]
    preferred_types: list[str] | None = None


GOALS = {'ftp_up', 'sprint', 'p5min', 'climbing', 'endurance', 'long_rides', 'allround'}


def _validate_power_zones(zones: list) -> list:
    """Leistungszonen validieren: 1..8 Zonen, sortierte Grenzen in %FTP (0..300)."""
    if not isinstance(zones, list) or not (1 <= len(zones) <= 8):
        raise HTTPException(400, 'power_zones muss eine Liste mit 1..8 Zonen sein')
    clean = []
    for i, z in enumerate(zones):
        try:
            low = float(z['low'])
            high = float(z['high'])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, f'Zone {i + 1}: low/high müssen Zahlen sein')
        if not (0 <= low < high <= 300):
            raise HTTPException(400, f'Zone {i + 1}: es gilt 0 <= low < high <= 300 (Prozent FTP)')
        name = str(z.get('name') or f'Zone {i + 1}')[:60]
        clean.append({'no': i + 1, 'name': name, 'low': low, 'high': high})
    prev_high = None
    for z in clean:
        if prev_high is not None and abs(z['low'] - prev_high) > 0.001:
            raise HTTPException(400, 'Zonengrenzen müssen lückenlos aneinandergrenzen')
        prev_high = z['high']
    return clean


@router.get('/settings')
def get_settings(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    ftp, ftp_src = services.current_ftp(con)
    wt, _ = services.current_weight(con)
    zones = json.loads(services.get_setting(con, 'power_zones', json.dumps(config.POWER_ZONES)))
    return {
        'ftp': ftp, 'ftp_source': ftp_src, 'weight': wt,
        'goal': services.get_setting(con, 'goal', 'allround'),
        'max_duration_min': int(services.get_setting(con, 'max_duration_min', 120)),
        'available_days': json.loads(services.get_setting(con, 'available_days', '[1,1,1,1,1,1,1]')),
        'units': services.get_setting(con, 'units', 'metric'),
        'timezone': services.get_setting(con, 'timezone', 'Europe/Berlin'),
        'power_zones': zones,
        'events': json.loads(services.get_setting(con, 'events', '[]')),
        'preferred_types': json.loads(services.get_setting(con, 'preferred_types', '[]')),
        'ftp_history': services.full_history(con, 'ftp'),
        'weight_history': services.full_history(con, 'weight'),
    }


@router.put('/settings')
def put_settings(body: SettingBody, _: str = Depends(require_auth),
                 con: sqlite3.Connection = Depends(get_db)):
    eff = body.effective_from or date.today().isoformat()
    changed_calc = False
    if body.ftp is not None:
        services.add_history(con, 'ftp', body.ftp, eff, body.source if body.source in ('manual', 'test') else 'manual')
        services.set_setting(con, 'ftp', body.ftp)
        changed_calc = True
    if body.weight is not None:
        services.add_history(con, 'weight', body.weight, eff, 'manual')
        services.set_setting(con, 'weight', body.weight)
        changed_calc = True
    if body.goal is not None:
        if body.goal not in GOALS:
            raise HTTPException(400, f'Unbekanntes Ziel. Erlaubt: {sorted(GOALS)}')
        services.set_setting(con, 'goal', body.goal)
    if body.max_duration_min is not None:
        services.set_setting(con, 'max_duration_min', body.max_duration_min)
    if body.available_days is not None:
        services.set_setting(con, 'available_days', json.dumps([1 if d else 0 for d in body.available_days]))
    if body.units:
        services.set_setting(con, 'units', body.units)
    if body.timezone:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(body.timezone)
        except Exception:
            raise HTTPException(400, f'Unbekannte Zeitzone: {body.timezone}')
        services.set_setting(con, 'timezone', body.timezone)
    if body.power_zones is not None:
        services.set_setting(con, 'power_zones', json.dumps(_validate_power_zones(body.power_zones)))
    if body.events is not None:
        clean_ev = []
        for ev in body.events[:50]:
            name = str(ev.get('name') or '').strip()[:120]
            d = str(ev.get('date') or '')
            try:
                date.fromisoformat(d)
            except ValueError:
                raise HTTPException(400, f'Event-Datum ungültig (YYYY-MM-DD erwartet): {d!r}')
            if name:
                clean_ev.append({'name': name, 'date': d})
        services.set_setting(con, 'events', json.dumps(clean_ev))
    if body.preferred_types is not None:
        allowed = set(coach.WORKOUT_LIBRARY)
        bad = [t for t in body.preferred_types if t not in allowed]
        if bad:
            raise HTTPException(400, f'Unbekannte Trainingstypen: {bad}')
        services.set_setting(con, 'preferred_types', json.dumps(body.preferred_types[:12]))
    if changed_calc:
        recalc_all_load(con)
        mmp_store.recompute_best_efforts(con)
    return {'ok': True}


def recalc_all_load(con: sqlite3.Connection) -> None:
    """IF/TSS aller Aktivitäten mit gültigem FTP zum Aktivitätsstart neu berechnen."""
    rows = con.execute("SELECT id, np_power, start_time, moving_time_s, duration_s FROM activities").fetchall()
    for r in rows:
        ftp, _src = services.ftp_for_activity_time(con, r['start_time'])
        dur = r['moving_time_s'] or r['duration_s']
        ifv = calc.intensity_factor(r['np_power'], ftp)
        tss_v = calc.tss(r['np_power'], dur, ftp)
        con.execute("UPDATE activities SET if_value=?, tss=? WHERE id=?", (ifv, tss_v, r['id']))
    con.commit()


# ---------------- Dashboard --------------------------------------------------

@router.get('/dashboard')
def dashboard(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    today = coach._today(con)
    out = {'ranges': {}, 'fitness': None, 'empty': True}
    counts = con.execute("SELECT COUNT(*) c FROM activities").fetchone()
    if counts and counts['c']:
        out['empty'] = False
    for name, days in (('7d', 7), ('28d', 28), ('month', None), ('year', None), ('all', None)):
        if name == 'month':
            start = today.replace(day=1)
        elif name == 'year':
            start = today.replace(month=1, day=1)
        elif name == 'all':
            row = con.execute("SELECT MIN(substr(start_time,1,10)) m FROM activities").fetchone()
            start = date.fromisoformat(row['m']) if row['m'] else today
        else:
            start = today - timedelta(days=days - 1)
        r = con.execute(
            """SELECT COUNT(*) rides, COALESCE(SUM(distance_m),0)/1000 km,
                      COALESCE(SUM(COALESCE(moving_time_s,duration_s,0)),0) time_s,
                      COALESCE(SUM(elevation_gain_m),0) elev,
                      AVG(avg_power) avg_p, SUM(work_kj) work, SUM(COALESCE(tss,0)) tss
               FROM activities WHERE substr(start_time,1,10) BETWEEN ? AND ?""",
            (start.isoformat(), today.isoformat())).fetchone()
        out['ranges'][name] = {
            'rides': r['rides'], 'km': round(r['km'], 1),
            'hours': round((r['time_s'] or 0) / 3600, 1),
            'elev_m': round(r['elev']), 'avg_power': round(r['avg_p'], 1) if r['avg_p'] else None,
            'work_kj': round(r['work']) if r['work'] else None,
            'tss': round(r['tss'] or 0, 0)}
    ftp, ftp_src = services.current_ftp(con)
    wt, _ = services.current_weight(con)
    out['ftp'] = {'w': ftp, 'source': ftp_src, 'wkg': calc.wkg(ftp, wt)}
    fit = coach.load_fitness(con)
    if fit:
        out['fitness'] = {'ctl': fit['ctl'], 'atl': fit['atl'], 'tsb': fit['tsb']}
        out['fitness_series'] = [{'date': x[0], 'tss': x[1], 'ctl': x[2], 'atl': x[3], 'tsb': x[4]}
                                 for x in fit['series'][-90:]]
    out['best_efforts'] = [dict(x) for x in con.execute(
        """SELECT b.duration_s, b.watts, b.wkg, b.achieved_on, a.name, a.id activity_id
           FROM best_efforts b LEFT JOIN activities a ON a.id=b.activity_id
           ORDER BY b.duration_s""")]
    try:
        rd = coach.readiness(con)
        out['readiness'] = {k: rd[k] for k in ('score', 'label', 'category', 'confidence')}
        out['readiness_factors'] = rd['factors'][:5]
        lastrec = con.execute("SELECT payload FROM recommendations ORDER BY id DESC LIMIT 1").fetchone()
        if lastrec:
            p = json.loads(lastrec['payload'])
            if p.get('day') == today.isoformat():
                out['today_recommendation'] = {
                    'name': p['recommendation']['name'],
                    'goal': p['recommendation']['goal'],
                    'duration_min': p['recommendation']['duration_min'],
                    'zone': p['recommendation'].get('zone')}
    except Exception as e:
        out['coach_error'] = str(e)
    planned = con.execute(
        "SELECT day,title,status FROM workouts WHERE status='planned' AND day>=? ORDER BY day LIMIT 7",
        (today.isoformat(),)).fetchall()
    out['planned'] = [dict(p) for p in planned]
    return out


# ---------------- Aktivitätenliste ------------------------------------------

@router.get('/activities')
def list_activities(from_: str | None = None, to: str | None = None,
                    type: str | None = None, power: str | None = None,
                    indoor: str | None = None, min_km: float | None = None,
                    max_km: float | None = None, min_elev: float | None = None,
                    sort: str = 'date', dirn: str = 'desc',
                    limit: int = 200, offset: int = 0,
                    _: str = Depends(require_auth),
                    con: sqlite3.Connection = Depends(get_db)):
    q = ["SELECT * FROM activities WHERE 1=1"]
    args: list = []
    if from_:
        q.append("AND substr(start_time,1,10)>=?"); args.append(from_)
    if to:
        q.append("AND substr(start_time,1,10)<=?"); args.append(to)
    if type:
        q.append("AND activity_type=?"); args.append(type)
    if power == 'yes':
        q.append("AND power_data=1"); args.append(1)
    elif power == 'no':
        q.append("AND power_data=0"); args.append(0)
    if indoor == 'yes':
        q.append("AND indoor=?"); args.append(1)
    elif indoor == 'no':
        q.append("AND indoor=?"); args.append(0)
    if min_km is not None:
        q.append("AND distance_m/1000.0>=?"); args.append(min_km)
    if max_km is not None:
        q.append("AND distance_m/1000.0<=?"); args.append(max_km)
    if min_elev is not None:
        q.append("AND elevation_gain_m>=?"); args.append(min_elev)
    allowed_sort = {'date': 'start_time', 'distance': 'distance_m', 'duration': 'duration_s',
                    'elev': 'elevation_gain_m', 'power': 'avg_power', 'np': 'np_power',
                    'hr': 'avg_hr', 'cadence': 'avg_cadence', 'work': 'work_kj',
                    'if': 'if_value', 'tss': 'tss', 'speed': 'avg_speed', 'name': 'name'}
    col = allowed_sort.get(sort, 'start_time')
    order = 'ASC' if dirn.lower().startswith('a') else 'DESC'
    q.append(f"ORDER BY {col} {order}, id DESC LIMIT ? OFFSET ?")
    args.extend([min(limit, 1000), max(offset, 0)])
    rows = con.execute(" ".join(q)).fetchall()
    total = con.execute("SELECT COUNT(*) c FROM activities").fetchone()['c']
    types = [r['activity_type'] for r in con.execute(
        "SELECT DISTINCT activity_type FROM activities ORDER BY 1")]
    return {'items': [dict(r) for r in rows], 'total': total, 'types': types}


@router.get('/activities/{act_id}')
def activity_detail(act_id: int, _: str = Depends(require_auth),
                    con: sqlite3.Connection = Depends(get_db)):
    row = con.execute("SELECT * FROM activities WHERE id=?", (act_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Aktivität nicht gefunden')
    laps = [dict(r) for r in con.execute(
        "SELECT * FROM laps WHERE activity_id=? ORDER BY lap_no", (act_id,))]
    segs = [dict(r) for r in con.execute(
        "SELECT * FROM segments WHERE activity_id=? ORDER BY start_epoch", (act_id,))]
    mmp = {r['duration_s']: r['watts'] for r in con.execute(
        "SELECT duration_s, watts FROM mmp_entries WHERE activity_id=?", (act_id,))}
    ftp, _ = services.ftp_for_activity_time(con, row['start_time'])
    wt, _ = services.weight_for_time(con, row['start_time'])
    journal = con.execute("SELECT notes,rpe,conditions FROM journals WHERE activity_id=?", (act_id,)).fetchone()
    return {'activity': dict(row), 'laps': laps, 'segments': segs, 'mmp': mmp,
            'ftp_used': ftp, 'weight_used': wt,
            'wkg_avg': calc.wkg(row['avg_power'], wt),
            'journal': dict(journal) if journal else None}


@router.get('/activities/{act_id}/series')
def activity_series(act_id: int, max_points: int = 2000,
                    _: str = Depends(require_auth),
                    con: sqlite3.Connection = Depends(get_db)):
    s = mmp_store.load_series(con, act_id)
    if not s:
        raise HTTPException(404, 'Keine Messreihe gespeichert')
    out = mmp_store.downsample(s['t'], {k: v for k, v in s.items() if k != 'n'}, max_points)
    out['n_original'] = s['n']
    return out


@router.get('/activities/{act_id}/power_curve')
def activity_power_curve(act_id: int, _: str = Depends(require_auth),
                         con: sqlite3.Connection = Depends(get_db)):
    s = mmp_store.load_series(con, act_id)
    if not s or not s['power']:
        return {'curve': [], 'note': 'Keine Wattdaten in dieser Aktivität'}
    res = calc.mmp_all_durations(s['t'], s['power'], mmp_store.MMP_DURATIONS)
    return {'curve': [{'dur': d, 'watts': w} for d, (w, _, _) in sorted(res.items())]}


@router.post('/activities/{act_id}/reprocess')
def reprocess(act_id: int, _: str = Depends(require_auth),
              con: sqlite3.Connection = Depends(get_db)):
    res = services.reprocess_activity(con, act_id)
    if res.get('status') != 'ok':
        raise HTTPException(400, res.get('message', 'Fehlgeschlagen'))
    return res


class JournalBody(BaseModel):
    notes: str | None = Field(None, max_length=4000)
    rpe: int | None = Field(None, ge=1, le=10)
    conditions: str | None = Field(None, max_length=500)


@router.post('/activities/{act_id}/journal')
def save_journal(act_id: int, body: JournalBody, _: str = Depends(require_auth),
                 con: sqlite3.Connection = Depends(get_db)):
    if not con.execute("SELECT 1 FROM activities WHERE id=?", (act_id,)).fetchone():
        raise HTTPException(404)
    con.execute("""INSERT INTO journals(activity_id,notes,rpe,conditions) VALUES(?,?,?,?)
                   ON CONFLICT(activity_id) DO UPDATE SET notes=excluded.notes,
                     rpe=excluded.rpe, conditions=excluded.conditions""",
                (act_id, body.notes, body.rpe, body.conditions))
    con.commit()
    return {'ok': True}


# ---------------- Belastung --------------------------------------------------

@router.get('/load')
def load(days: int = 180, _: str = Depends(require_auth),
         con: sqlite3.Connection = Depends(get_db)):
    days = max(30, min(days, 730))
    today = coach._today(con)
    fit = coach.load_fitness(con, lookback_days=days)
    if not fit:
        return {'series': [], 'note': 'Noch keine Daten'}
    start_iso = (today - timedelta(days=days - 1)).isoformat()
    weekly = con.execute(
        """SELECT strftime('%Y-W%W', start_time) w, SUM(COALESCE(tss,0)) tss,
                  SUM(distance_m)/1000 km, COUNT(*) n FROM activities
           WHERE substr(start_time,1,10) >= ?
           GROUP BY w ORDER BY w DESC LIMIT 26""", (start_iso,)).fetchall()
    return {'series': [{'date': x[0], 'tss': x[1], 'ctl': x[2], 'atl': x[3], 'tsb': x[4]}
                       for x in fit['series']],
            'current': {'ctl': fit['ctl'], 'atl': fit['atl'], 'tsb': fit['tsb']},
            'weekly': [dict(r) for r in reversed(weekly)],
            'formulas': {'CTL_tau_Tage': calc.CTL_TAU, 'ATL_tau_Tage': calc.ATL_TAU,
                         'TSB': 'CTL(t-1) - ATL(t-1)',
                         'TSS': '100*s*NP*IF/(FTP*3600)', 'IF': 'NP/FTP',
                         'NP': '4.Wurzel aus Mittel der 30s-Fenster^4'}}


# ---------------- Coach ------------------------------------------------------

class CheckinBody(BaseModel):
    day: str | None = None
    sleep_hours: float | None = Field(None, ge=0, le=24)
    sleep_quality: int | None = Field(None, ge=1, le=5)
    fatigue: int | None = Field(None, ge=1, le=5)
    soreness: int | None = Field(None, ge=1, le=5)
    motivation: int | None = Field(None, ge=1, le=5)
    wellness: int | None = Field(None, ge=1, le=5)
    symptoms: str | None = Field(None, max_length=500)
    notes: str | None = Field(None, max_length=2000)


@router.post('/coach/checkin')
def checkin(body: CheckinBody, _: str = Depends(require_auth),
            con: sqlite3.Connection = Depends(get_db)):
    day = body.day or coach._today(con).isoformat()
    con.execute("""INSERT INTO checkins(day,sleep_hours,sleep_quality,fatigue,soreness,
                   motivation,wellness,symptoms,notes) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(day) DO UPDATE SET sleep_hours=excluded.sleep_hours,
                   sleep_quality=excluded.sleep_quality, fatigue=excluded.fatigue,
                   soreness=excluded.soreness, motivation=excluded.motivation,
                   wellness=excluded.wellness, symptoms=excluded.symptoms,
                   notes=excluded.notes""",
                (day, body.sleep_hours, body.sleep_quality, body.fatigue, body.soreness,
                 body.motivation, body.wellness, body.symptoms, body.notes))
    con.commit()
    return {'ok': True, 'day': day}


@router.get('/coach/readiness')
def readiness(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    return coach.readiness(con)


@router.get('/coach/today')
def today_rec(refresh: bool = False, _: str = Depends(require_auth),
              con: sqlite3.Connection = Depends(get_db)):
    day = coach._today(con).isoformat()
    row = con.execute("SELECT payload FROM recommendations WHERE day=?", (day,)).fetchone()
    if row and not refresh:
        return json.loads(row['payload'])
    return coach.recommend_today(con)


@router.get('/coach/plan')
def get_plan(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    row = con.execute("SELECT payload FROM plans ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return coach.generate_week_plan(con, reason='Ersterstellung')
    return json.loads(row['payload'])


@router.post('/coach/plan/regenerate')
def regen_plan(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    return coach.generate_week_plan(con, reason='Manuelle Neuberechnung')


class PlanEvent(BaseModel):
    type: str = Field(pattern='^(skipped|extra_hard|completed)$')
    date: str
    title: str | None = None
    tss: float | None = None


@router.post('/coach/plan/event')
def plan_event(ev: PlanEvent, _: str = Depends(require_auth),
               con: sqlite3.Connection = Depends(get_db)):
    if ev.type == 'skipped':
        con.execute("UPDATE workouts SET status='skipped' WHERE day=? AND status='planned'",
                    (ev.date,))
        con.commit()
    return coach.replan_after_change(con, ev.model_dump())


@router.get('/coach/workouts')
def workout_library(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    ftp, _ = services.current_ftp(con)
    mmp5_row = con.execute("SELECT watts FROM best_efforts WHERE duration_s=300").fetchone()
    mmp5 = mmp5_row['watts'] if mmp5_row else None
    wt, _ = services.current_weight(con)
    items = []
    for kind in coach.WORKOUT_LIBRARY:
        wo = coach.build_workout(kind, ftp, mmp5, coach._default_len(kind, 120), wt)
        items.append(wo)
    saved = [dict(r) for r in con.execute(
        "SELECT * FROM workouts WHERE status='library' OR day IS NULL")]
    return {'generated': items, 'saved': saved}


class WorkoutSave(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    goal: str | None = None
    duration_min: int | None = Field(None, ge=5, le=600)
    intensity_zone: str | None = None
    structure: list | dict | None = None
    est_tss: float | None = None


@router.post('/coach/workouts/save')
def save_workout(body: WorkoutSave, _: str = Depends(require_auth),
                 con: sqlite3.Connection = Depends(get_db)):
    con.execute("""INSERT INTO workouts(title,goal,duration_min,intensity_zone,structure,est_tss,status)
                   VALUES(?,?,?,?,?,?,'library')""",
                (body.title, body.goal, body.duration_min, body.intensity_zone,
                 json.dumps(body.structure), body.est_tss))
    con.commit()
    return {'ok': True}


@router.get('/coach/progress')
def progress(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    return coach.progress_analysis(con)


class ChatBody(BaseModel):
    question: str = Field(min_length=2, max_length=500)


@router.post('/coach/chat')
def chat(body: ChatBody, _: str = Depends(require_auth),
         con: sqlite3.Connection = Depends(get_db)):
    return coach.chat_answer(con, body.question)


@router.get('/coach/history')
def coach_history(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    recs = con.execute("SELECT day,payload FROM recommendations ORDER BY id DESC LIMIT 14").fetchall()
    hist = [{'day': r['day'], **(json.loads(r['payload']).get('readiness') or {})} for r in recs]
    cis = [dict(r) for r in con.execute("SELECT * FROM checkins ORDER BY day DESC LIMIT 28")]
    return {'recommendations': hist, 'checkins': cis}


# ---------------- Leistungskurve global -------------------------------------

@router.get('/power-curve')
def global_power_curve(days: int | None = None, _: str = Depends(require_auth),
                       con: sqlite3.Connection = Depends(get_db)):
    rows = con.execute(
        """SELECT b.duration_s, b.watts, b.wkg, a.name, a.start_time
           FROM best_efforts b LEFT JOIN activities a ON a.id=b.activity_id
           ORDER BY b.duration_s""").fetchall()
    curve = []
    wt_now, _ = services.current_weight(con)
    for r in rows:
        when = r['start_time']
        wt_hist, _ = services.weight_for_time(con, when) if when else (wt_now, 'current')
        curve.append({'dur': r['duration_s'], 'watts': r['watts'],
                      'wkg': calc.wkg(r['watts'], wt_hist or wt_now),
                      'activity': r['name'], 'date': when[:10] if when else None})
    ftp, _ = services.current_ftp(con)
    return {'curve': curve, 'ftp': ftp,
            'durations': mmp_store.MMP_DURATIONS}


@router.get('/best-efforts')
def best_efforts(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    rows = con.execute(
        """SELECT b.*, a.name, a.start_time, a.distance_m FROM best_efforts b
           LEFT JOIN activities a ON a.id=b.activity_id ORDER BY b.duration_s""").fetchall()
    return [dict(r) for r in rows]


# ---------------- Zonen / Zeiten in Zonen ------------------------------------

@router.get('/zones')
def zones_summary(days: int = 28, _: str = Depends(require_auth),
                  con: sqlite3.Connection = Depends(get_db)):
    ftp, _ = services.current_ftp(con)
    zone_defs = json.loads(services.get_setting(con, 'power_zones', json.dumps(config.POWER_ZONES)))
    result = [0.0] * len(zone_defs)
    if ftp:
        today = coach._today(con)
        since = (today - timedelta(days=days)).isoformat()
        rows = con.execute(
            """SELECT a.id FROM activities a WHERE substr(a.start_time,1,10)>=? AND a.power_data=1""",
            (since,)).fetchall()
        for r in rows:
            s = mmp_store.load_series(con, r['id'])
            if not s or not s['power']:
                continue
            tz = calc.time_in_zones(s['t'], s['power'], ftp)
            for i, v in enumerate(tz[:len(result)]):
                result[i] += v
    return {'zones': zone_defs, 'ftp': ftp,
            'seconds': [round(x) for x in result],
            'minutes': [round(x / 60, 1) for x in result], 'days': days}


# ---------------- Import -----------------------------------------------------

MAX_UPLOAD_BYTES = config.MAX_FILE_BYTES


@router.post('/import')
def import_files(files: list[UploadFile] = File(...),
                 _: str = Depends(require_auth),
                 con: sqlite3.Connection = Depends(get_db)):
    report = []
    for f in files:
        name = (f.filename or 'upload').split('/')[-1]
        # Upload streamingartig in Häppchen lesen, Grenze früh erkennen
        chunks = []
        total = 0
        too_big = False
        while True:
            chunk = f.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                too_big = True
                break
            chunks.append(chunk)
        if too_big:
            report.append({'filename': name, 'status': 'error',
                           'message': f'Datei überschreitet {MAX_UPLOAD_BYTES // 1048576} MB Limit'})
            continue
        data = b''.join(chunks)
        try:
            results = services.import_upload(con, name, data)
        except Exception as e:
            results = [{'filename': name, 'status': 'error', 'message': f'Interner Fehler: {e}'}]
        report.extend(results)
    ok = sum(1 for r in report if r['status'] == 'ok')
    dup = sum(1 for r in report if r['status'] == 'duplicate')
    err = sum(1 for r in report if r['status'] == 'error')
    return {'report': report, 'summary': {'ok': ok, 'duplicates': dup, 'errors': err}}


@router.get('/import/log')
def import_log(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    rows = con.execute("SELECT * FROM import_log ORDER BY id DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


# ---------------- Datenexport / Löschung -------------------------------------

@router.get('/export')
def export_data(_: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    out = {}
    for table in ('activities', 'settings', 'settings_history', 'checkins',
                  'plans', 'workouts', 'recommendations', 'best_efforts',
                  'laps', 'segments', 'journals'):
        out[table] = [dict(r) for r in con.execute(f"SELECT * FROM {table}")]
    return JSONResponse(out, headers={'Content-Disposition': 'attachment; filename="cycling-export.json"'})


@router.post('/delete-all')
def delete_all(password: str = Form(...), confirm: str = Form(...),
               user: str = Depends(require_auth), con: sqlite3.Connection = Depends(get_db)):
    if confirm != 'DELETE':
        raise HTTPException(400, 'Bestätigung fehlt (confirm=DELETE)')
    row = con.execute("SELECT pw_hash FROM users WHERE username=?", (user,)).fetchone()
    if not row or not services.verify_password(row['pw_hash'], password):
        raise HTTPException(401, 'Passwort falsch')
    backup = db.backup_database()
    for table in ('activities', 'series', 'laps', 'segments', 'mmp_entries',
                  'best_efforts', 'checkins', 'plans', 'workouts', 'recommendations',
                  'journals', 'route_matches', 'import_log'):
        con.execute(f"DELETE FROM {table}")
    con.commit()
    return {'ok': True, 'backup': str(backup)}


# ---------------- Strava-API Status (dokumentiert) ---------------------------

@router.get('/strava/info')
def strava_info(_: str = Depends(require_auth)):
    return {
        'mode': 'file_import',
        'assessment': (
            'Die offizielle Strava API (OAuth 2.0, scope activity:read_all) erlaubt das '
            'Abrufen eigener Activities für private Analyse-Anwendungen und ist damit '
            'grundsätzlich zulässig. Aber: Der Standard-Zugriff liefert nur Zusammenfassungen '
            '+ Stream-Daten mit Einschränkungen (Rate Limits ~200 Anträge/15 min & 4000/h; '
            'Streams dürfen gemäß API Terms temporär, aber die dauerhafte Zweit-Auswertung '
            'bedürftiger Vollaufbereitung ist beim Sport-only Access kritisch geprüft worden). '
            'Für diesen privaten Anwendungsfall ist der Datei-Export-Import daher der primäre, '
            'robuste Weg; eine OAuth-Anbindung kann optional ergänzt werden, ohne Scraping.'),
        'primary_import': 'Strava-Datenexport (ZIP) / Einzeldateien FIT, FIT.GZ, TCX, GPX',
    }
