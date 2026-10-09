"""Berechnungen: Normalized Power, IF, TSS, CTL/ATL/TSB, MMP (Power Curve), Zonen.

Alle Formeln sind dokumentiert und werden ausschließlich aus gespeicherten
Messreihen bzw. hinterlegten FTP-/Gewichtswerten berechnet. Fehlende Eingaben
führen zu None ("kann nicht berechnet"), nie zu erfundenen Werten.

Formeln (Standard nach Hunter Allen / Andrew Coggan, wie in TrainingPeaks):
- NP  : 30-Sekunden-gleitender Durchschnitt der Watt, davon 4. Wurzel,
        quadriert -> NP = (mean(x_i^4))^(1/4) über 30s-Mittelwerte.
        Datenlücken (Pause/keine Samples) werden NICHT übersprungen: ein
        Zeitfenster gilt nur, wenn die Spanne <= Fenster + 2*Medianintervall.
- XPP : "Extended Power" als Ersatz für fehlende NP bei sehr kurzen Einheiten;
        wir verwenden NP exakt ab >= 30 s Daten, sonst None.
- IF  : Intensity Factor = NP / FTP   (IF = None ohne NP oder FTP)
- TSS : 100 * (Dauer[s] * NP * IF) / (FTP * 3600)
- kJ  : Arbeit = sum(power * dt)
- CTL : Chronic Training Load, exponentiell gewichtete tägliche TSS mit
        Zeitkonstanter tau_CTL = 42 Tage:
        CTL_t = CTL_{t-1} + (TSS_t - CTL_{t-1}) / 42
- ATL : Acute Training Load, tau_ATL = 7 Tage:
        ATL_t = ATL_{t-1} + (TSS_t - ATL_{t-1}) / 7
- TSB : Training Stress Balance = CTL_{gestern} - ATL_{gestern}
        (TSB vor der heutigen Einheit; TrainingPeaks-Konvention).
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections import deque

CTL_TAU = 42.0   # Tage
ATL_TAU = 7.0    # Tage
NP_WINDOW_S = 30.0
GAP_TOLERANCE_S = 2.0  # Toleranz für Abweichung vom Median-Samplingintervall


def median(vals: list[float]) -> float | None:
    vs = sorted(v for v in vals if v is not None and isinstance(v, (int, float)))
    if not vs:
        return None
    n = len(vs)
    mid = n // 2
    if n % 2:
        return float(vs[mid])
    return (vs[mid - 1] + vs[mid]) / 2.0


def normalized_power(times: list[float], power: list[float | None],
                     window_s: float = NP_WINDOW_S) -> float | None:
    """Coggan NP aus unregelmäßigen Zeitstempeln. None wenn < window_s Daten."""
    pts = [(t, p) for t, p in zip(times, power)
           if p is not None and isinstance(p, (int, float))]
    if len(pts) < 2:
        return None
    total_span = pts[-1][0] - pts[0][0]
    if total_span < window_s:
        return None
    dts = [pts[i + 1][0] - pts[i][0] for i in range(len(pts) - 1)]
    med_dt = median(dts) or 1.0
    max_gap = med_dt * (1 + GAP_TOLERANCE_S) + 0.5
    # 30s-Fenster-Durchschnitte (zeitbasiert, kein Überschätzen über Lücken)
    win_means: list[tuple[float, float]] = []  # (end_time, mean_watts)
    dq: deque[tuple[float, float]] = deque()
    acc = 0.0
    i = 0
    for idx, (t, p) in enumerate(pts):
        dq.append((t, p)); acc += p
        while dq and (t - dq[0][0]) > window_s:
            acc -= dq.popleft()[1]
        span = t - dq[0][0]
        if span >= window_s - 1e-9 or (idx == len(pts) - 1 and span > 0):
            # Fenster gültig, wenn volle Breite erreicht ODER Dateiende
            gap_ok = all((dq[j + 1][0] - dq[j][0]) <= max_gap for j in range(len(dq) - 1))
            if gap_ok and span >= window_s * 0.9:
                win_means.append((t, acc / len(dq)))
    if len(win_means) < 2:
        return None
    fourth = sum(m ** 4 for _, m in win_means) / len(win_means)
    return round(fourth ** 0.25, 1)


def intensity_factor(np_: float | None, ftp: float | None) -> float | None:
    if np_ is None or not ftp:
        return None
    return round(np_ / ftp, 3)


def tss(np_: float | None, duration_s: float | None, ftp: float | None) -> float | None:
    """TSS = 100 * s * NP * IF / (FTP * 3600)."""
    if np_ is None or duration_s is None or not ftp:
        return None
    if_ = np_ / ftp
    return round(100.0 * duration_s * np_ * if_ / (ftp * 3600.0), 1)


def work_kj(times: list[float], power: list[float | None]) -> float | None:
    """Arbeit in kJ via Trapezregel über Zeitstempel; None ohne Wattdaten."""
    pts = [(t, p) for t, p in zip(times, power) if p is not None]
    if len(pts) < 2:
        return None
    acc = 0.0
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        acc += (p0 + p1) / 2.0 * (t1 - t0)
    return round(acc / 1000.0, 1)


def mmp_all_durations(times: list[float], power: list[float | None],
                      durations: list[int]) -> dict[int, tuple[float, int, int]]:
    """Maximale mittlere Leistung pro Dauer (Sekunden).

    Returns {dur: (watts, start_idx, end_idx)}; nur Fenster, deren Zeitspanne
    <= dur + Toleranz ist (keine Bestleistung über Datenlücken hinweg).
    """
    pts = [(t, p) for t, p in zip(times, power) if p is not None]
    out: dict[int, tuple[float, int, int]] = {}
    if len(pts) < 2:
        return out
    ts = [t for t, _ in pts]
    ps = [p for _, p in pts]
    pre = [0.0] * (len(ps) + 1)
    for i, p in enumerate(ps):
        pre[i + 1] = pre[i] + p
    dts = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    med_dt = median(dts) or 1.0
    tol = med_dt * 1.5 + 0.5
    for dur in durations:
        best = 0.0
        bi = bj = -1
        j = 0
        for i in range(len(ts)):
            if ts[i] + dur > ts[-1] + 1e-9:
                break
            # Ende-index: letzter Punkt mit t <= ts[i]+dur
            target = ts[i] + dur
            j = max(j, i)
            while j + 1 < len(ts) and ts[j + 1] <= target + 1e-9:
                j += 1
            span = ts[j] - ts[i]
            if span < dur - tol:
                continue
            # Lückenerkennung: kein Intervall im Fenster darf > max_gap sein
            max_gap_allowed = med_dt * (1 + GAP_TOLERANCE_S) + 0.5
            ok = True
            for k in range(i, j):
                if ts[k + 1] - ts[k] > max_gap_allowed:
                    ok = False
                    break
            if not ok:
                continue
            avg = (pre[j + 1] - pre[i]) / span if span > 0 else 0
            # Bei Überabdeckung (letzter Punkt deutlich nach Ziel) anteilig kürzen:
            if span > dur + tol:
                continue
            if avg > best:
                best = avg
                bi, bj = i, j
        if bi >= 0 and best > 0:
            out[dur] = (round(best, 1), bi, bj)
    return out


# ---- Belastungsverlauf ------------------------------------------------------

def fitness_series(daily_tss: dict[str, float | None],
                   start_date, end_date) -> list[tuple]:
    """Berechnet (date, tss, ctl, atl, tsb) für jeden Tag im Bereich.

    daily_tss: {'YYYY-MM-DD': tss_sum}. CTL/ATL rekursiv über alle Tage inkl.
    Ruhetage (TSS 0). TSB = CTL(t-1) - ATL(t-1).
    """
    from datetime import timedelta
    dates = []
    d = start_date
    while d <= end_date:
        dates.append(d)
        d += timedelta(days=1)
    ctl = atl = 0.0
    rows = []
    prev_ctl = prev_atl = 0.0
    for d in dates:
        key = d.strftime('%Y-%m-%d')
        t = daily_tss.get(key) or 0.0
        prev_ctl, prev_atl = ctl, atl
        ctl += (t - ctl) / CTL_TAU
        atl += (t - atl) / ATL_TAU
        rows.append((key, t, round(ctl, 1), round(atl, 1), round(prev_ctl - prev_atl, 1)))
    return rows


def zone_bounds_from_pct(zones: list[dict], ftp: float) -> list[tuple[float, float]]:
    return [(z['low'] * ftp / 100.0 if z.get('low') is not None else 0.0,
             z['high'] * ftp / 100.0 if z.get('high') is not None else float('inf'))
            for z in zones]


def time_in_zones(times: list[float], power: list[float | None],
                  ftp: float) -> list[float]:
    """Sekunden pro Leistungszone (5 Zonen) über Trapez-Zuordnung."""
    bounds = [(0, 0.55), (0.55, 0.75), (0.75, 0.90), (0.90, 1.05), (1.05, 3.0)]
    res = [0.0] * 5
    if not ftp:
        return res
    pts = [(t, p) for t, p in zip(times, power) if p is not None]
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        dt = t1 - t0
        pm = (p0 + p1) / 2.0 / ftp
        for zi, (lo, hi) in enumerate(bounds):
            if lo <= pm < hi:
                res[zi] += dt
                break
    return [round(x, 1) for x in res]


def wkg(watts: float | None, weight_kg: float | None) -> float | None:
    if watts is None or not weight_kg:
        return None
    return round(watts / weight_kg, 2)


def ramp_mmp_estimate(mmp: dict[int, float]) -> dict[str, float | None]:
    """Klassische RAMP-Schätzung aus 5s/1min/5min: FTP ≈ 0.75*P_5min (Coggan)."""
    p5 = mmp.get(300)
    p1 = mmp.get(60)
    p5s = mmp.get(5)
    est = None
    if p5:
        est = round(p5 * 0.75)
    return {'p5s': p5s, 'p1min': p1, 'p5min': p5, 'ftp_est_5min': est}
