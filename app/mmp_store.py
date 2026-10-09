"""MMP-Speicherung (max. mittlere Leistungen) und Bestleistungen (PB).

Dauern: 1,2,3,5,6,8,10,15,20,30,40,50,60,70,90,120,180,240,300,360,420,480,540,
600,720,840,900,1200,1500,1800,2700,3600,5400,7200 Sekunden.
"""
from __future__ import annotations

import json
import sqlite3

from . import calc

MMP_DURATIONS = [1, 2, 3, 5, 6, 8, 10, 15, 20, 30, 40, 50, 60, 90,
                 120, 150, 180, 240, 300, 360, 420, 480, 540, 600,
                 720, 900, 1200, 1500, 1800, 2700, 3600, 5400, 7200]


def update_mmp_and_pb(con: sqlite3.Connection, activity_id: int,
                      times, power, start_iso: str, weight: float | None):
    res = calc.mmp_all_durations(times, power, MMP_DURATIONS)
    for dur, (watts, si, ei) in res.items():
        con.execute("INSERT INTO mmp_entries(activity_id,duration_s,watts) VALUES(?,?,?)",
                    (activity_id, dur, watts))
        pb = con.execute("SELECT watts FROM best_efforts WHERE duration_s=?", (dur,)).fetchone()
        if pb is None or watts > pb['watts']:
            w_ = calc.wkg(watts, weight)
            con.execute("""INSERT INTO best_efforts(duration_s,activity_id,watts,wkg,achieved_on)
                           VALUES(?,?,?,?,?)
                           ON CONFLICT(duration_s) DO UPDATE SET
                             activity_id=excluded.activity_id, watts=excluded.watts,
                             wkg=excluded.wkg, achieved_on=excluded.achieved_on""",
                        (dur, activity_id, round(watts, 1), w_, start_iso[:10]))


def recompute_best_efforts(con: sqlite3.Connection) -> int:
    """PBs aus gespeicherten MMP-Einträgen komplett neu bestimmen (nach FTP/Gewicht-Änderung)."""
    con.execute("DELETE FROM best_efforts")
    rows = con.execute("""SELECT m.duration_s d, m.watts w, a.start_time st, a.id aid
                          FROM mmp_entries m JOIN activities a ON a.id=m.activity_id
                          ORDER BY m.duration_s, m.watts DESC""").fetchall()
    n = 0
    for r in rows:
        existing = con.execute("SELECT 1 FROM best_efforts WHERE duration_s=?", (r['d'],)).fetchone()
        if existing:
            continue
        when = r['st']
        w_, _ = None, None
        from .services import weight_for_time
        wt, _src = weight_for_time(con, when)
        wkg = calc.wkg(r['w'], wt)
        con.execute("INSERT INTO best_efforts(duration_s,activity_id,watts,wkg,achieved_on)"
                    " VALUES(?,?,?,?,?)", (r['d'], r['aid'], r['w'], wkg, when[:10]))
        n += 1
    con.commit()
    return n


def load_series(con: sqlite3.Connection, activity_id: int) -> dict | None:
    row = con.execute("SELECT * FROM series WHERE activity_id=?", (activity_id,)).fetchone()
    if not row:
        return None
    def j(colname):
        v = row[colname]
        return json.loads(v) if v else []
    return {'n': row['n'], 't': j('epoch'), 'time': j('time'), 'power': j('power'),
            'hr': j('hr'), 'cadence': j('cadence'), 'speed': j('speed'),
            'lat': j('lat'), 'lon': j('lon'), 'alt': j('alt'), 'temp': j('temp')}


def downsample(t, series_dict, max_points=2000):
    """Equal-stride Reduktion für Chart-Daten; Originaldaten bleiben unberührt."""
    n = len(t)
    if n <= max_points:
        stride = 1
    else:
        stride = -(-n // max_points)
    out = {}
    for k, v in series_dict.items():
        if isinstance(v, list) and len(v) == n:
            out[k] = v[::stride]
        else:
            out[k] = v
    return out
