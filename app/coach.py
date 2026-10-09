"""Personal Cycling Coach — regelbasiert, vollständig datenbasiert.

Grundlagen (alle Werte aus der Datenbank, keine erfundenen Daten):
- Readiness: gewichtete Summe aus TSB, ATL/CTL-Verhältnis, Erholungsstatus,
  aufeinanderfolgenden Trainingstagen, Abstand zur letzten intensiven Einheit
  und subjektivem Check-in. Fehlende Check-ins werden NICHT als "erholt"
  gewertet (Neutralwert + expliziter Hinweis).
- Empfehlung: Entscheidungsbaum über Readiness-Kategorie, Belastungsspitzen
  der letzten 7 Tage, verfügbare Tage, Trainingsziel und Wochenbudget.
- Workout-Generator: Strukturierte Blöcke (warmup/work/rest/cooldown) mit
  Watt-Zielen aus FTP bzw. Power-Dauer-Kurve (z.B. VO2 = bestes 5min-MMP).
- 7-Tage-Plan: sequenzielle Simulation; ausgelassene/zusätzliche Einheiten
  führen zu dokumentierter Neubewertung (Gründe bleiben im Plan-JSON).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import calc, db, services

INTENSITY_TSS = 250.0   # Ab dieser Tages-TSS-Summen gilt ein Tag als "intensiv"


def _today(con) -> date:
    tzname = services.get_setting(con, 'timezone', 'Europe/Berlin')
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tzname)).date()
    except Exception:
        return datetime.now(timezone.utc).date()


def daily_tss_map(con, start: date, end: date) -> dict[str, float]:
    rows = con.execute(
        """SELECT substr(start_time,1,10) d, SUM(COALESCE(tss,0)) t
           FROM activities WHERE substr(start_time,1,10) BETWEEN ? AND ?
           GROUP BY d""", (start.isoformat(), end.isoformat())).fetchall()
    return {r['d']: float(r['t'] or 0) for r in rows}


def load_fitness(con, lookback_days=180):
    """Berechnet CTL/ATL/TSB-Historie bis heute."""
    today = _today(con)
    start = today - timedelta(days=lookback_days)
    daily = daily_tss_map(con, start, today)
    rows = calc.fitness_series(daily, start, today)
    if not rows:
        return None
    last = rows[-1]
    return {'date': last[0], 'tss': last[1], 'ctl': last[2], 'atl': last[3],
            'tsb': last[4], 'series': rows}


def recent_intensity(con, days=7):
    today = _today(con)
    since = (today - timedelta(days=days)).isoformat()
    rows = con.execute(
        """SELECT substr(start_time,1,10) d, COALESCE(tss,0) tss, np_power, if_value,
                  duration_s FROM activities WHERE substr(start_time,1,10)>=?""",
        (since,)).fetchall()
    per_day: dict[str, float] = {}
    max_if = None
    hard_count = 0
    for r in rows:
        per_day[r['d']] = per_day.get(r['d'], 0) + (r['tss'] or 0)
        if r['if_value'] and (max_if is None or r['if_value'] > max_if):
            max_if = r['if_value']
    hard_days = [d for d, t in per_day.items() if t >= INTENSITY_TSS * 0.9]
    return {'per_day': per_day, 'week_tss': sum(per_day.values()),
            'max_if_7d': max_if, 'hard_days': sorted(hard_days),
            'consecutive': _consecutive_training_days(per_day, today)}


def _consecutive_training_days(per_day: dict[str, float], ref_today: date | None = None) -> int:
    """Aufeinanderfolgende Trainingstage bis gestern (TSS > 50 pro Tag)."""
    n = 0
    d = (ref_today or date.today()) - timedelta(days=1)
    while per_day.get(d.isoformat(), 0) > 50:
        n += 1
        d -= timedelta(days=1)
    return n


def days_since_last_hard(con) -> int | None:
    today = _today(con)
    row = con.execute(
        """SELECT substr(start_time,1,10) d FROM activities
           WHERE COALESCE(tss,0) >= ? OR COALESCE(if_value,0) >= 0.90
           ORDER BY start_time DESC LIMIT 1""", (INTENSITY_TSS,)).fetchone()
    if not row:
        return None
    d = date.fromisoformat(row['d'])
    return (today - d).days


def get_checkin(con, day: str) -> dict | None:
    row = con.execute("SELECT * FROM checkins WHERE day=?", (day,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
#  B. Readiness / Ermüdung
# ---------------------------------------------------------------------------

def readiness(con) -> dict:
    """Regelbasierte Readiness mit nachvollziehbaren Faktoren.

    Score 0..100. Kein physiologischer Messwert, sondern ein Modell — wird im
    UI auch so gekennzeichnet. Unsicherheit steigt mit fehlenden Daten.
    """
    factors: list[dict] = []
    score = 50.0
    missing: list[str] = []

    fit = load_fitness(con)
    if fit:
        tsb, ctl, atl = fit['tsb'], fit['ctl'], fit['atl']
        if tsb >= 15:
            score += 15; factors.append({'name': 'TSB', 'value': tsb,
                                         'effect': '+15', 'text': f'TSB {tsb:+.0f}: gut erholt (Form)'})
        elif tsb >= 5:
            score += 8; factors.append({'name': 'TSB', 'value': tsb, 'effect': '+8',
                                        'text': f'TSB {tsb:+.0f}: leicht positiv'})
        elif tsb >= -10:
            factors.append({'name': 'TSB', 'value': tsb, 'effect': '+0',
                            'text': f'TSB {tsb:+.0f}: neutral belastet'})
        elif tsb >= -25:
            score -= 12; factors.append({'name': 'TSB', 'value': tsb, 'effect': '-12',
                                         'text': f'TSB {tsb:+.0f}: akkumulierte Müdigkeit'})
        else:
            score -= 22; factors.append({'name': 'TSB', 'value': tsb, 'effect': '-22',
                                         'text': f'TSB {tsb:+.0f}: hohe akute Erschöpfung'})
        ratio = atl / ctl if ctl else None
        if ratio is not None:
            if ratio > 1.5:
                score -= 10; factors.append({'name': 'ATL/CTL', 'value': round(ratio, 2),
                                             'effect': '-10',
                                             'text': f'ATL/CTL {ratio:.2f} > 1.5: frische Spitzentragung'})
            elif ratio < 0.7:
                score += 5; factors.append({'name': 'ATL/CTL', 'value': round(ratio, 2),
                                            'effect': '+5', 'text': f'ATL/CTL {ratio:.2f}: akute Last niedrig'})
    else:
        missing.append('Keine Belastungshistorie (CTL/ATL/TSB nicht berechenbar)')

    ri = recent_intensity(con, days=7)
    if ri['max_if_7d'] and ri['max_if_7d'] >= 0.95:
        score -= 8
        factors.append({'name': 'Letzte Intensität', 'value': ri['max_if_7d'], 'effect': '-8',
                        'text': f"IF {ri['max_if_7d']:.2f} in den letzten 7 Tagen (sehr hoch)"})
    d_hard = days_since_last_hard(con)
    if d_hard is not None:
        if d_hard <= 1:
            score -= 10
            factors.append({'name': 'Intervall-Abstand', 'value': d_hard, 'effect': '-10',
                            'text': 'Intensive Einheit gestern'})
        elif d_hard >= 3:
            score += 6
            factors.append({'name': 'Intervall-Abstand', 'value': d_hard, 'effect': '+6',
                            'text': f'{d_hard} Tage seit letzter intensiver Einheit'})
    if ri['consecutive'] >= 3:
        score -= 6
        factors.append({'name': 'Trainingsfolge', 'value': ri['consecutive'], 'effect': '-6',
                        'text': f"{ri['consecutive']} aufeinanderfolgende Trainingstage"})

    ci = get_checkin(con, _today(con).isoformat())
    if ci:
        subj = 0
        notes = []
        if ci.get('sleep_hours') is not None:
            sh = float(ci['sleep_hours'])
            if sh >= 7.5:
                subj += 6; notes.append(f"Schlaf {sh:.1f} h (+)")
            elif sh < 6:
                subj -= 8; notes.append(f"Schlaf {sh:.1f} h (-)")
        def scale(v, invert=False, weight=4):
            if v is None:
                return 0
            v = int(v)
            x = (3 - v) if invert else (v - 3)
            return x * weight
        subj += scale(ci.get('fatigue'), invert=True)
        subj += scale(ci.get('soreness'), invert=True)
        subj += scale(ci.get('wellness'))
        subj += scale(ci.get('sleep_quality'))
        subj += scale(ci.get('motivation'))
        score += max(-20, min(20, subj))
        factors.append({'name': 'Check-in', 'value': subj,
                        'effect': f"{subj:+d}", 'text': "; ".join(notes) or 'Subjektive Werte eingeflossen'})
        if ci.get('symptoms'):
            score -= 25
            factors.append({'name': 'Krankheit', 'value': ci['symptoms'], 'effect': '-25',
                            'text': f"Notierte Symptome: {ci['symptoms']}"})
    else:
        missing.append('Kein Tages-Check-in: subjektive Erholung unbekannt '
                       '(weder als erholt noch als müde gewertet)')

    score = max(0, min(100, round(score)))
    if score >= 70:
        cat = 'gut_erholt'
        label = 'Gut erholt'
    elif score >= 50:
        cat = 'normal'
        label = 'Normal belastet'
    elif score >= 30:
        cat = 'erhoehte_ermuedung'
        label = 'Erhöhte Ermüdung'
    else:
        cat = 'erholung_priorisieren'
        label = 'Erholung priorisieren'
    confidence = 'hoch' if not missing else ('mittel' if len(missing) == 1 else 'niedrig')
    return {'score': score, 'category': cat, 'label': label,
            'factors': factors, 'missing': missing, 'confidence': confidence,
            'fitness': {k: fit[k] for k in ('ctl', 'atl', 'tsb')} if fit else None,
            'note': 'Modellbasis: TSB/ATL/CTL + Intervallmuster + Check-in. '
                    'Keine physiologische Messung.'}


# ---------------------------------------------------------------------------
#  C/D. Empfehlung & Workouts
# ---------------------------------------------------------------------------

WORKOUT_LIBRARY = {
    'rest_day':      {'name': 'Ruhetag', 'goal': 'Erholung'},
    'recovery':      {'name': 'Regenerative Fahrt', 'goal': 'Aktive Erholung', 'zone': 'Z1'},
    'endurance':     {'name': 'Grundlagenausdauer', 'goal': 'Aerobe Basis', 'zone': 'Z2'},
    'long_endurance':{'name': 'Lange Ausdauerfahrt', 'goal': 'Landausdauer', 'zone': 'Z2'},
    'tempo':         {'name': 'Tempoeinheit', 'goal': 'Schwellennahe Ausdauer', 'zone': 'Z3'},
    'sweetspot':     {'name': 'Sweet Spot', 'goal': 'Effiziente Schwelle', 'zone': 'Z3/4'},
    'threshold':     {'name': 'Schwellenintervalle', 'goal': 'FTP steigern', 'zone': 'Z4'},
    'vo2':           {'name': 'VO2max-Intervalle', 'goal': 'VO2max', 'zone': 'Z5'},
    'anaerobic':     {'name': 'Kurze intensive Intervalle', 'goal': 'Anaerobe Kapazität', 'zone': 'Z5+'},
    'sprint':        {'name': 'Sprinttraining', 'goal': 'Neuromuskulär/Sprint', 'zone': 'Z6/7'},
    'climbs':        {'name': 'Bergintervalle', 'goal': 'Bergleistung', 'zone': 'Z3-4'},
}

GOAL_PREFERENCES = {
    'ftp_up': ['threshold', 'sweetspot', 'vo2'],
    'sprint': ['sprint', 'anaerobic'],
    'p5min': ['vo2', 'threshold'],
    'climbing': ['climbs', 'sweetspot', 'threshold'],
    'endurance': ['endurance', 'long_endurance'],
    'long_rides': ['long_endurance', 'endurance'],
    'allround': ['threshold', 'endurance', 'vo2'],
}


def _mmp_for(con, dur: int) -> float | None:
    row = con.execute("SELECT watts FROM best_efforts WHERE duration_s=?", (dur,)).fetchone()
    return row['watts'] if row else None


def build_workout(kind: str, ftp: float | None, mmp5: float | None,
                  duration_min: int, weight: float | None = None) -> dict:
    """Strukturiertes Workout mit Wattzielen aus FTP/MMP. None-Watt wenn FTP fehlt."""
    lib = WORKOUT_LIBRARY[kind]
    blocks: list[dict] = []
    est_tss = None

    def pw(lo_pct, hi_pct, base=None):
        b = base or ftp
        if not b:
            return None, None
        return round(b * lo_pct), round(b * hi_pct)

    warm = {'type': 'warmup', 'minutes': 15, 'target': 'locker bis Z2'}
    cool = {'type': 'cooldown', 'minutes': 10, 'target': 'locker ausfahren'}

    if kind == 'rest_day':
        return {'kind': kind, 'name': lib['name'], 'goal': lib['goal'],
                'duration_min': 0, 'blocks': [], 'est_tss': 0, 'zone': '-'}
    if kind == 'recovery':
        lo, hi = pw(0.40, 0.55)
        blocks = [{'type': 'steady', 'minutes': duration_min,
                   'watt_low': lo, 'watt_high': hi, 'target': 'Z1 (<55% FTP)'}]
        est_tss = round(duration_min * 60 * 100 * (0.45 * (ftp or 0)) ** 2 / ((ftp or 1) ** 2 * 3600), 0) if ftp else None
    elif kind in ('endurance', 'long_endurance'):
        lo, hi = pw(0.55, 0.75)
        blocks = [{'type': 'steady', 'minutes': duration_min,
                   'watt_low': lo, 'watt_high': hi, 'target': 'Z2 (56–75% FTP)'}]
        if ftp:
            np_est = 0.68 * ftp
            est_tss = calc.tss(np_est, duration_min * 60, ftp)
    elif kind == 'tempo':
        lo, hi = pw(0.76, 0.90)
        rest_lo, rest_hi = pw(0.50, 0.60)
        n = max(1, round((duration_min - 25) / 15))
        blocks = [warm]
        for i in range(n):
            blocks.append({'type': 'work', 'minutes': 10, 'watt_low': lo, 'watt_high': hi,
                           'target': 'Z3 Tempo (76–90% FTP)', 'rep': i + 1})
            blocks.append({'type': 'rest', 'minutes': 5, 'watt_low': rest_lo,
                           'watt_high': rest_hi, 'target': 'locker', 'rep': i + 1})
        blocks.append(cool)
        if ftp:
            est_tss = calc.tss(0.83 * ftp, n * 10 * 60, ftp)
    elif kind == 'sweetspot':
        lo, hi = pw(0.84, 0.94)
        n = max(2, round((duration_min - 25) / 12))
        blocks = [warm]
        for i in range(n):
            blocks.append({'type': 'work', 'minutes': 10, 'watt_low': lo, 'watt_high': hi,
                           'target': 'Sweet Spot (84–94% FTP)', 'rep': i + 1})
            blocks.append({'type': 'rest', 'minutes': max(2, min(5, duration_min // 8)),
                           'target': 'Erholung locker', 'rep': i + 1})
        blocks.append(cool)
        if ftp:
            est_tss = calc.tss(0.89 * ftp, n * 10 * 60, ftp)
    elif kind == 'threshold':
        lo, hi = pw(0.95, 1.05)
        n = max(2, min(4, round((duration_min - 25) / 12)))
        blocks = [warm]
        for i in range(n):
            blocks.append({'type': 'work', 'minutes': 8, 'watt_low': lo, 'watt_high': hi,
                           'target': 'Schwelle (95–105% FTP)', 'rep': i + 1})
            blocks.append({'type': 'rest', 'minutes': 4, 'target': 'Erholung (locker)', 'rep': i + 1})
        blocks.append(cool)
        if ftp:
            est_tss = calc.tss(1.0 * ftp, n * 8 * 60, ftp)
    elif kind == 'vo2':
        base = mmp5 or (ftp * 1.15 if ftp else None)
        if base:
            lo, hi = round(base * 0.98), round(base * 1.07)
        else:
            lo = hi = None
        n = max(3, min(6, round((duration_min - 25) / 7)))
        blocks = [warm]
        for i in range(n):
            blocks.append({'type': 'work', 'minutes': 3, 'watt_low': lo, 'watt_high': hi,
                           'target': '~100–115% FTP / bestes 5min-MMP', 'rep': i + 1})
            blocks.append({'type': 'rest', 'minutes': 3, 'target': 'gleich lange Erholung', 'rep': i + 1})
        blocks.append(cool)
        if ftp:
            est_tss = calc.tss(min(1.15 * ftp, base or 1.15 * ftp), n * 3 * 60, ftp)
    elif kind == 'anaerobic':
        lo, hi = pw(1.10, 1.30)
        n = max(4, min(8, round((duration_min - 25) / 4)))
        blocks = [warm]
        for i in range(n):
            blocks.append({'type': 'work', 'minutes': 1, 'watt_low': lo, 'watt_high': hi,
                           'target': '1 min hart (110–130% FTP)', 'rep': i + 1})
            blocks.append({'type': 'rest', 'minutes': 2, 'target': '2 min locker', 'rep': i + 1})
        blocks.append(cool)
        if ftp:
            est_tss = calc.tss(1.2 * ftp, n * 60, ftp)
    elif kind == 'sprint':
        n = max(4, min(8, round((duration_min - 25) / 5)))
        blocks = [dict(warm, minutes=20)]
        for i in range(n):
            blocks.append({'type': 'work', 'seconds': 10, 'watt_low': None, 'watt_high': None,
                           'target': 'All-out Sprint 10 s (stehend/sitzend abwechselnd)', 'rep': i + 1})
            blocks.append({'type': 'rest', 'minutes': 4, 'target': 'vollständig erholen', 'rep': i + 1})
        blocks.append(cool)
        est_tss = round(n * 8, 0)
    elif kind == 'climbs':
        lo, hi = pw(0.85, 1.00)
        n = max(2, min(5, round((duration_min - 25) / 12)))
        blocks = [warm]
        for i in range(n):
            blocks.append({'type': 'work', 'minutes': 8, 'watt_low': lo, 'watt_high': hi,
                           'target': 'Berg hoch, Leistung konstant (85–100% FTP)', 'rep': i + 1})
            blocks.append({'type': 'rest', 'minutes': 6, 'target': 'Talfahrt locker', 'rep': i + 1})
        blocks.append(cool)
        if ftp:
            est_tss = calc.tss(0.92 * ftp, n * 8 * 60, ftp)
    total_min = sum(b.get('minutes', 0) for b in blocks) + sum(b.get('seconds', 0) for b in blocks) / 60
    return {'kind': kind, 'name': lib['name'], 'goal': lib['goal'], 'zone': lib['zone'],
            'duration_min': int(total_min), 'blocks': blocks, 'est_tss': est_tss,
            'requires_ftp': any(b.get('watt_low') is None and b['type'] == 'work' for b in blocks) and not ftp}


def recommend_today(con) -> dict:
    """Die zentrale Frage: Was soll ich heute fahren? Inkl. Begründung+Alternative."""
    rd = readiness(con)
    ftp, ftp_src = services.current_ftp(con)
    weight, _ = services.current_weight(con)
    mmp5 = _mmp_for(con, 300)
    goal = services.get_setting(con, 'goal', 'allround')
    max_min = int(services.get_setting(con, 'max_duration_min', 120))
    available = json.loads(services.get_setting(con, 'available_days', '[1,1,1,1,1,1,1]'))
    dow = _today(con).isoweekday()  # 1=Mo .. 7=So
    prefs = GOAL_PREFERENCES.get(goal, GOAL_PREFERENCES['allround'])
    ri = recent_intensity(con, 7)

    reasons = [f"Readiness {rd['score']}/100 ({rd['label']})"]
    reasons += [f['text'] for f in rd['factors'][:4]]
    if rd['missing']:
        reasons += [f"Unbekannt: {m}" for m in rd['missing']]

    if not ftp:
        rec_kind = 'endurance'
        note = ('Kein FTP hinterlegt: Wattziele können nicht berechnet werden. '
                'Empfehlung basiert auf Herzfrequenz/empfundener Anstrengung. '
                'Bitte FTP in den Einstellungen setzen.')
    else:
        note = None
        if rd['category'] == 'erholung_priorisieren':
            rec_kind = 'rest_day' if (rd['score'] < 20 or _has_symptoms(con)) else 'recovery'
        elif rd['category'] == 'erhoehte_ermuedung':
            rec_kind = 'recovery' if ri['consecutive'] >= 3 else 'endurance'
        elif rd['category'] == 'normal':
            if ri['hard_days'] and len(ri['hard_days']) >= 2:
                rec_kind = 'endurance'
            else:
                rec_kind = prefs[1] if len(prefs) > 1 else 'endurance'
        else:  # gut_erholt
            rec_kind = prefs[0]

    # Verfügbare Trainingstage beachten
    if not available[dow - 1] and rec_kind != 'rest_day':
        rec_kind = 'rest_day'
        reasons.append(f"Heute (Wochentag {dow}) ist laut Einstellungen kein Trainingstag.")

    workout = build_workout(rec_kind, ftp, mmp5, min(max_min, _default_len(rec_kind, max_min)), weight)
    alt_kind = 'recovery' if rec_kind not in ('rest_day', 'recovery') else 'endurance'
    alternative = build_workout(alt_kind, ftp, mmp5, 60, weight)

    payload = {
        'day': _today(con).isoformat(),
        'readiness': {'score': rd['score'], 'label': rd['label'],
                      'category': rd['category'], 'confidence': rd['confidence']},
        'recommendation': workout,
        'alternative': alternative,
        'reasons': reasons,
        'ftp': ftp, 'ftp_source': ftp_src,
        'week_tss_so_far': round(ri['week_tss'], 0),
        'note': note,
    }
    con.execute("INSERT INTO recommendations(day,payload) VALUES(?,?)",
                (_today(con).isoformat(), json.dumps(payload)))
    con.commit()
    return payload


def _has_symptoms(con) -> bool:
    ci = get_checkin(con, _today(con).isoformat())
    return bool(ci and ci.get('symptoms'))


def _default_len(kind: str, cap: int) -> int:
    defaults = {'recovery': 45, 'endurance': 90, 'long_endurance': 150, 'tempo': 75,
                'sweetspot': 75, 'threshold': 75, 'vo2': 60, 'anaerobic': 50,
                'sprint': 45, 'climbs': 90}
    return min(cap, defaults.get(kind, 60))


# ---------------------------------------------------------------------------
#  E. 7-Tage-Plan (adaptiv)
# ---------------------------------------------------------------------------

def generate_week_plan(con, reason: str | None = None) -> dict:
    """Simuliert die nächsten 7 Tage tageweise inkl. erwarteter Belastung.

    Regeln: max. 2 intensive Tage pro Woche, mind. 1 Ruhetag, keine 3 harten
    Tage in Folge, Vorzug der Ziel-Prioritäten an als verfügbar markierten Tagen.
    """
    ftp, _ = services.current_ftp(con)
    weight, _ = services.current_weight(con)
    mmp5 = _mmp_for(con, 300)
    goal = services.get_setting(con, 'goal', 'allround')
    max_min = int(services.get_setting(con, 'max_duration_min', 120))
    available = json.loads(services.get_setting(con, 'available_days', '[1,1,1,1,1,1,1]'))
    prefs = GOAL_PREFERENCES.get(goal, GOAL_PREFERENCES['allround'])
    today = _today(con)
    ri = recent_intensity(con, 7)
    rd = readiness(con)

    sim_ctl = (rd.get('fitness') or {}).get('ctl', 0)
    sim_atl = (rd.get('fitness') or {}).get('atl', 0)
    hard_this_week = len(ri['hard_days'])
    plan_days: list[dict] = []
    prev_hard = False
    consec = ri['consecutive']

    intensity_kinds = {'tempo', 'sweetspot', 'threshold', 'vo2', 'anaerobic', 'sprint', 'climbs'}
    pref_i = 0
    for off in range(7):
        d = today + timedelta(days=off)
        dow = d.isoweekday()
        tsb_sim = sim_ctl - sim_atl
        # Entscheidung für den Tag
        if not available[dow - 1]:
            kind, why = 'rest_day', 'Kein Trainingstag laut Einstellungen.'
        elif off == 0:
            rec = recommend_today(con)
            kind = rec['recommendation']['kind']
            why = '; '.join(rec['reasons'][:3])
        else:
            fatigue_now = tsb_sim < -25 or (prev_hard and consec >= 2)
            if hard_this_week >= 2 and prev_hard:
                kind, why = 'recovery', 'Belastungsmanagement: max. 2 intensive Einheiten pro Woche.'
            elif fatigue_now:
                kind, why = ('rest_day' if tsb_sim < -35 else 'recovery',
                             f'Simulierte Form negativ (TSB {tsb_sim:+.0f}), Erholungspuffer nötig.')
            elif prev_hard:
                kind, why = 'endurance', 'Lockere Einheit nach intensiver Belastung.'
            else:
                kind = prefs[pref_i % len(prefs)]
                pref_i += 1
                why = f"Ziel '{goal}': nächste Priorität im Wechsel mit Grundlagenblöcken."
                if off in (3, 6):
                    kind, why = 'endurance', 'Grundlagenblock zur Stabilität zwischen Intensitäten.'
        wo = build_workout(kind, ftp, mmp5, _default_len(kind, max_min), weight)
        add_tss = wo.get('est_tss') or 0
        sim_ctl += (add_tss - sim_ctl) / calc.CTL_TAU
        sim_atl += (add_tss - sim_atl) / calc.ATL_TAU
        is_hard = kind in intensity_kinds
        if is_hard:
            hard_this_week += 1
            consec += 1
        elif kind == 'rest_day':
            consec = 0
        prev_hard = is_hard
        plan_days.append({
            'date': d.isoformat(), 'weekday': d.strftime('%a'),
            'kind': kind, 'title': wo['name'], 'goal': wo['goal'],
            'duration_min': wo['duration_min'], 'zone': wo.get('zone'),
            'structure': wo.get('blocks', []), 'est_tss': wo.get('est_tss'),
            'priority': 'hoch' if is_hard else ('mittel' if kind == 'endurance' else 'niedrig'),
            'reason': why,
        })
    payload = {'week_start': today.isoformat(), 'generated': datetime.now(timezone.utc).isoformat(),
               'trigger_reason': reason, 'days': plan_days,
               'rules': ['max. 2 intensive Einheiten/Woche', 'mind. 1 Ruhetag',
                         'keine 3 harten Tage in Folge', 'intensive Einheiten >= 48h auseinander',
                         'Zielprioritäten werden an verfügbaren Tagen verteilt']}
    week_monday = (today - timedelta(days=today.weekday())).isoformat()
    con.execute("INSERT INTO plans(week_start,payload) VALUES(?,?)",
                (week_monday, json.dumps(payload)))
    # geplante Workouts aktualisieren
    for pd in plan_days:
        if pd['kind'] != 'rest_day':
            con.execute("""INSERT INTO workouts(day,title,goal,duration_min,intensity_zone,
                        structure,est_tss,status,reason)
                        VALUES(?,?,?,?,?,?,?,'planned',?)
                        ON CONFLICT(day,title) DO UPDATE SET
                          duration_min=excluded.duration_min, structure=excluded.structure,
                          est_tss=excluded.est_tss, reason=excluded.reason""",
                (pd['date'], pd['title'], pd['goal'], pd['duration_min'], pd['zone'],
                 json.dumps(pd['structure']), pd['est_tss'], pd['reason']))
    con.commit()
    return payload


def replan_after_change(con, event: dict) -> dict:
    """Nach ausgelassener oder zusätzlicher Einheit: Plan neu bewerten (dokumentiert)."""
    kind = event.get('type')  # 'skipped' | 'extra_hard' | 'completed'
    if kind == 'skipped':
        reason = f"Ausgelassene Einheit am {event.get('date')}: Restwoche wird entlastet."
    elif kind == 'extra_hard':
        reason = (f"Zusätzliche intensive Fahrt am {event.get('date')} "
                  f"(TSS {event.get('tss','?')}): verbleibende harte Einheiten werden reduziert.")
    else:
        reason = f"Einheit {event.get('date')} absolviert: Plan angepasst."
    return generate_week_plan(con, reason=reason)


# ---------------------------------------------------------------------------
#  F. Fortschrittsanalyse
# ---------------------------------------------------------------------------

PROGRESS_DURATIONS = [5, 60, 300, 1200, 3600]   # Sprint/1min/5min/20min/60min


def progress_analysis(con, window_days=90) -> dict:
    """Vergleich PB-Leistungen letztes Fenster vs. vorheriges Fenster."""
    out = []
    today = _today(con)
    cur_start = (today - timedelta(days=window_days)).isoformat()
    prev_start = (today - timedelta(days=2 * window_days)).isoformat()
    for dur in PROGRESS_DURATIONS:
        cur = con.execute(
            """SELECT MAX(watts) w FROM mmp_entries m JOIN activities a ON a.id=m.activity_id
               WHERE m.duration_s=? AND substr(a.start_time,1,10) >= ?""",
            (dur, cur_start)).fetchone()['w']
        prev = con.execute(
            """SELECT MAX(watts) w FROM mmp_entries m JOIN activities a ON a.id=m.activity_id
               WHERE m.duration_s=? AND substr(a.start_time,1,10) BETWEEN ? AND ?""",
            (dur, prev_start, cur_start)).fetchone()['w']
        label = {5: '5 s Sprint', 60: '1 min', 300: '5 min', 1200: '20 min', 3600: '60 min'}[dur]
        delta = round(cur - prev, 1) if (cur and prev) else None
        status = 'verbessert' if (delta and delta > 2) else ('rückläufig' if (delta and delta < -2)
                 else 'stabil' if delta is not None else 'unbekannt (Datenlücke)')
        out.append({'duration_s': dur, 'label': label, 'current_w': cur,
                    'previous_w': prev, 'delta_w': delta, 'status': status,
                    'evidence': 'berechnete MMP-Werte aus Messreihen'})
    vol = con.execute(
        """SELECT substr(start_time,1,7) m, SUM(distance_m)/1000 km, SUM(COALESCE(tss,0)) tss,
                  COUNT(*) n FROM activities GROUP BY m ORDER BY m DESC LIMIT 6""").fetchall()
    volume = [dict(r) for r in reversed(vol)]
    return {'power_trends': out, 'monthly_volume': volume,
            'note': 'Bestätigt = gemessene Wattwerte; Schlussfolgerungen bleiben '
                    'modelbasiert und von Datenqualität abhängig.'}


# ---------------------------------------------------------------------------
#  G. Coach-Chat (regelbasiert, ohne externen KI-Dienst)
# ---------------------------------------------------------------------------

def chat_answer(con, question: str) -> dict:
    """Beantwortet Fragen ausschließlich mit gespeicherten Daten.

    Erkennungsregeln statt Raten: bei keiner passenden Regel sagt das System
    ehrlich, dass es die Frage (noch) nicht datenbasiert beantworten kann.
    """
    q = question.lower().strip()
    facts: list[str] = []
    answer = None

    def fmt_num(x, unit=''):
        return f"{x:.1f}{unit}" if isinstance(x, float) else f"{x}{unit}"

    if any(w in q for w in ('belastung', 'tss', 'stress', 'woche diese', 'diese woche')):
        ri = recent_intensity(con, 7)
        fit = load_fitness(con)
        parts = [f"Trainingsbelastung der letzten 7 Tage: {fmt_num(round(ri['week_tss'],0))} TSS."]
        if fit:
            parts.append(f"CTL {fit['ctl']}, ATL {fit['atl']}, TSB {fit['tsb']:+.0f}.")
        if ri['hard_days']:
            parts.append("Intensive Tage: " + ", ".join(ri['hard_days']) + ".")
        answer = " ".join(parts)
    elif (('stärkste' in q or 'staerkste' in q or 'beste' in q)
          and ('fünf' in q or 'funf' in q or '5 min' in q or '5min' in q)):
        row = con.execute(
            """SELECT m.watts, a.name, a.start_time, a.id FROM mmp_entries m
               JOIN activities a ON a.id=m.activity_id WHERE m.duration_s=300
               ORDER BY m.watts DESC LIMIT 1""").fetchone()
        if row:
            answer = (f"Stärkste 5-Minuten-Leistung: {row['watts']:.0f} W am "
                      f"{row['start_time'][:10]} bei „{row['name']}“ (Aktivität #{row['id']}).")
        else:
            answer = "Dafür liegen keine Wattdaten vor."
    elif 'heute' in q and any(w in q for w in ('fahren', 'einheit', 'training', 'empfehl')):
        rec = recommend_today(con)
        w = rec['recommendation']
        answer = (f"Heute empfohlen: {w['name']} ({w.get('duration_min',0)} min, Zone {w.get('zone','-')}). "
                  f"Begründung: {'; '.join(rec['reasons'][:3])}")
        if w.get('blocks'):
            detail = "; ".join(f"{b.get('minutes', b.get('seconds',0))}"
                               f"{'min' if 'minutes' in b else 's'} {b['type']}"
                               + (f" {b['watt_low']}-{b['watt_high']} W" if b.get('watt_low') else "")
                               for b in w['blocks'][:6])
            answer += f" Struktur: {detail}."
    elif any(w in q for w in ('warum', 'begründ', 'begruend')):
        rd = readiness(con)
        answer = (f"Einschätzung: {rd['label']} (Score {rd['score']}/100, "
                  f"Confidence {rd['confidence']}). Faktoren: "
                  + "; ".join(f['text'] for f in rd['factors']) )
        if rd['missing']:
            answer += ". Nicht bekannt: " + ", ".join(rd['missing'])
    elif 'entwickelt' in q or 'verändert' in q or 'veraendert' in q or 'fortschritt' in q:
        pr = progress_analysis(con, 28)
        lines = [f"{t['label']}: " + (f"{t['current_w']:.0f} W ({t['delta_w']:+.1f} W vs. Vorperiode)"
                 if t['current_w'] and t['delta_w'] is not None else
                 ("keine Daten" if not t['current_w'] else f"{t['current_w']:.0f} W"))
                 for t in pr['power_trends']]
        answer = "Leistungsverlauf letzte 4 Wochen gegen die 4 Wochen davor: " + " | ".join(lines)
    elif 'bereiche' in q or 'schwerpunkt' in q or 'trainieren' in q:
        pr = progress_analysis(con, 56)
        weak = [t['label'] for t in pr['power_trends'] if t['status'] in ('stabil', 'rückläufig')]
        strong = [t['label'] for t in pr['power_trends'] if t['status'] == 'verbessert']
        answer = ""
        if strong:
            answer += f"Verbesserungen gezeigt in: {', '.join(strong)}. "
        if weak:
            answer += (f"Stagnierend/rückläufig: {', '.join(weak)} — dort wären gezielte "
                       "Reize sinnvoll (siehe Trainingsbibliothek). ")
        if not answer:
            answer = "Zu wenig Vergleichsdaten für eine belastbare Aussage."
    elif 'wie unterscheiden' in q or 'vergleich' in q or 'selben strecke' in q or 'selbst strecke' in q:
        try:
            has_routes = con.execute("SELECT 1 FROM route_matches LIMIT 1").fetchone() is not None
        except Exception:
            has_routes = False
        answer = ("Strecken-Vergleich benötigt GPS-Segmentzuordnung (Modul Streckenanalyse); "
                  "dafür sind noch keine zwei Fahrten derselben Strecke erkannt worden."
                  if not has_routes else "Siehe Streckenanalyse: Zuordnung vorhanden.")
    else:
        n_act = con.execute("SELECT COUNT(*) c FROM activities").fetchone()['c']
        answer = (f"Diese Frage kann ich noch nicht sicher datenbasiert beantworten "
                  f"(Regeldecklücke). Im Bestand: {n_act} Aktivitäten. "
                  "Beispiele für unterstützte Fragen: Wochenbelastung, beste 5-Min-Fahrt, "
                  "Heutige Empfehlung, Leistungsverlauf, Stärken/Schwächen.")
    con.execute("INSERT INTO coach_messages(role,content) VALUES('user',?)", (question,))
    con.execute("INSERT INTO coach_messages(role,content) VALUES('assistant',?)", (answer,))
    con.commit()
    return {'answer': answer, 'facts': facts, 'source': 'db-regeln',
            'note': 'Alle Zahlen stammen aus der Datenbank; keine erfundenen Werte.'}
