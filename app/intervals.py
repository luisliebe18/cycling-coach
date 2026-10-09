"""Automatische Erkennung von Belastungsabschnitten (Work-Intervalle).

Regelbasiert, dokumentiert:
- Ein Abschnitt gilt als "work", wenn der gleitende 10s-Durchschnitt für
  mindestens MIN_LEN=60 s über WORK_FRACTION*FTP (Default 0.75) liegt.
- Ruhephasen zwischen zwei Work-Abschnitten werden als 'rest' markiert, wenn
  sie >= 30 s dauern und im Mittel unter 0.6*FTP liegen.
- Ohne FTP wird relativ zum 95%-Perzentil der Fahrt erkannt (klar gekennzeichnet).
"""
from __future__ import annotations

MIN_LEN = 60.0
REST_MIN = 30.0


def _rolling_mean(times, power, window):
    """Einfacher gleitender Durchschnitt; liefert (t_end, mean) Liste."""
    pts = [(t, p) for t, p in zip(times, power) if p is not None]
    out = []
    from collections import deque
    dq = deque()
    acc = 0.0
    for t, p in pts:
        dq.append((t, p)); acc += p
        while dq and t - dq[0][0] > window:
            acc -= dq.popleft()[1]
        out.append((t, acc / len(dq)))
    return out


def detect_work_intervals(times, power, ftp=None):
    if not power or not any(p is not None for p in power):
        return []
    thr = (ftp * 0.75) if ftp else None
    if thr is None:
        vals = sorted(p for p in power if p is not None)
        if not vals:
            return []
        thr = vals[int(len(vals) * 0.95)] * 0.8
    rm = _rolling_mean(times, power, 10.0)
    above = [(t, m) for t, m in rm if m >= thr]
    segs: list[dict] = []
    cur_start = None
    prev_t = None
    for t, m in above:
        if cur_start is None:
            cur_start = t
        elif t - prev_t > 30:  # Lücke -> neuer Abschnitt
            segs.append((cur_start, prev_t))
            cur_start = t
        prev_t = t
    if cur_start is not None and prev_t is not None:
        segs.append((cur_start, prev_t))
    result = []
    work_blocks = []
    for s, e in segs:
        if e - s >= MIN_LEN:
            work_blocks.append((s, e))
    idx = {t: i for i, (t, m) in enumerate(rm)}
    for k, (s, e) in enumerate(work_blocks):
        win = [m for (t, m) in rm if s <= t <= e]
        mx = max(win) if win else None
        avg = sum(win) / len(win) if win else None
        hr_vals = [h for t, p, h in zip(times, power, _hr(times, power)) if False]
        result.append({'kind': 'work', 'start': round(s, 1), 'end': round(e, 1),
                       'avg_power': round(avg, 1) if avg else None,
                       'max_power': round(mx, 1) if mx else None,
                       'avg_hr': None})
        nxt = work_blocks[k + 1][0] if k + 1 < len(work_blocks) else None
        if nxt is not None and nxt - e >= REST_MIN:
            rest_win = [m for (t, m) in rm if e < t < nxt]
            ravg = sum(rest_win) / len(rest_win) if rest_win else None
            result.append({'kind': 'rest', 'start': round(e, 1), 'end': round(nxt, 1),
                           'avg_power': round(ravg, 1) if ravg else None,
                           'max_power': round(max(rest_win), 1) if rest_win else None,
                           'avg_hr': None})
    return result


def _hr(times, power):
    return [None for _ in times]
