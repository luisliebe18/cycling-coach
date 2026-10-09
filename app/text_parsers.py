"""Parser für TCX (Training Center XML, Garmin) und GPX (GPS Exchange).

Verwendet xml.etree.ElementTree mit Namespace-Behandlung. Es werden nur Werte
übernommen, die tatsächlich in der Datei stehen; fehlende Werte bleiben None.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


class ActivityData:
    """Neutraler Datensatz aus einem Parser."""

    def __init__(self):
        self.name: str | None = None
        self.sport: str | None = None
        self.start_time: datetime | None = None
        self.duration_s: float | None = None
        self.distance_m: float | None = None
        self.elevation_gain_m: float | None = None
        self.avg_hr: float | None = None
        self.max_hr: float | None = None
        self.avg_cadence: float | None = None
        self.avg_speed: float | None = None
        self.work_kj: float | None = None
        self.device_info: str | None = None
        # Zeitreihe
        self.t: list[float] = []          # Sekunden ab Start
        self.iso: list[str] = []          # ISO-Zeitstempel
        self.power: list[float | None] = []
        self.hr: list[int | None] = []
        self.cadence: list[float | None] = []
        self.speed: list[float | None] = []
        self.lat: list[float | None] = []
        self.lon: list[float | None] = []
        self.alt: list[float | None] = []
        self.temp: list[float | None] = []
        # Runden
        self.laps: list[dict] = []

    @property
    def has_power(self) -> bool:
        return any(v is not None for v in self.power)

    @property
    def has_hr(self) -> bool:
        return any(v is not None for v in self.hr)

    @property
    def has_gps(self) -> bool:
        return any(v is not None for v in self.lat)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?', s)
        if not m:
            return None
        micro = int((m.group(7) or '0')[:6].ljust(6, '0'))
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                      int(m.group(4)), int(m.group(5)), int(m.group(6)), micro)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _f(text) -> float | None:
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
#  TCX
# ---------------------------------------------------------------------------

def parse_tcx(data: bytes) -> list[ActivityData]:
    root = ET.fromstring(data)
    ns = {'tc': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'}
    activities = root.findall('.//tc:Activity', ns)
    if not activities:  # Namespace-freie Dateien tolerieren
        activities = root.findall('.//Activity')
    out: list[ActivityData] = []
    for act in activities:
        a = ActivityData()
        attrs = act.attrib
        a.sport = attrs.get('Sport') or attrs.get('sport')
        laps = act.findall('tc:Lap', ns) or act.findall('Lap')
        trackpoints: list[tuple] = []
        total_dist = 0.0
        total_time = 0.0
        gain = 0.0
        last_alt = None
        work_j = 0.0
        for lap in laps:
            lstart = _parse_iso(lap.attrib.get('StartTime'))
            ldist = _f(_txt(lap, 'tc:DistanceMeters', ns) or _txt(lap, 'DistanceMeters'))
            ltime = _f(_txt(lap, 'tc:TotalTimeSeconds', ns) or _txt(lap, 'TotalTimeSeconds'))
            cal = _f(_txt(lap, 'tc:Calories', ns) or _txt(lap, 'Calories'))
            avghr = _f(_txt(lap, 'tc:AverageHeartRateBpm/tc:Value', ns)
                       or _txt(lap, 'AverageHeartRateBpm/Value'))
            maxhr = _f(_txt(lap, 'tc:MaximumHeartRateBpm/tc:Value', ns)
                       or _txt(lap, 'MaximumHeartRateBpm/Value'))
            cad = _f(_txt(lap, 'tc:AverageCadence', ns) or _txt(lap, 'AverageCadence'))
            lpowers: list[float] = []
            lhrs: list[float] = []
            lap_start_idx = len(trackpoints)
            for tp in lap.findall('.//tc:Trackpoint', ns) + lap.findall('.//Trackpoint'):
                ts = _parse_iso(_txt(tp, 'tc:Time', ns) or _txt(tp, 'Time'))
                alt = _f(_txt(tp, 'tc:AltitudeMeters', ns) or _txt(tp, 'AltitudeMeters'))
                d = _f(_txt(tp, 'tc:DistanceMeters', ns) or _txt(tp, 'DistanceMeters'))
                hr_el = _txt(tp, 'tc:HeartRateBpm/tc:Value', ns) or _txt(tp, 'HeartRateBpm/Value')
                hr = _f(hr_el)
                cad2 = _f(_txt(tp, 'tc:Cadence', ns) or _txt(tp, 'Cadence'))
                ext_ns = {'ext': 'http://garmin.com/xmlschemas/ActivityExtension/v2'}
                w_node = (tp.find('.//ext:Watts', ext_ns)
                          or tp.find('.//TPX/Watts') or tp.find('.//Watts'))
                watt = _f(w_node.text) if w_node is not None and w_node.text else None
                sp_node = (tp.find('.//ext:Speed', ext_ns)
                           or tp.find('.//TPX/Speed'))
                spd = _f(sp_node.text) if sp_node is not None and sp_node.text else None
                latlon = _gpx_latlon(tp, ns)
                if ts and a.start_time is None:
                    a.start_time = ts
                tsec = (ts - a.start_time).total_seconds() if (ts and a.start_time) else len(trackpoints)
                trackpoints.append((tsec, ts, watt, hr, cad2, spd,
                                    latlon[0], latlon[1], alt))
                if watt is not None:
                    lpowers.append(watt)
                    work_j += watt  # 1s Sampling-Annahme für TCX
                if hr is not None:
                    lhrs.append(hr)
                if d is not None:
                    total_dist = max(total_dist, d)
                if alt is not None:
                    if last_alt is not None and alt > last_alt:
                        gain += alt - last_alt
                    last_alt = alt
            lap_end_idx = len(trackpoints)
            lap_avg_p = sum(lpowers) / len(lpowers) if lpowers else None
            lap_max_p = max(lpowers) if lpowers else None
            lap_avg_hr = avghr if avghr is not None else (sum(lhrs) / len(lhrs) if lhrs else None)
            a.laps.append({
                'lap_no': len(a.laps) + 1,
                'start_epoch': trackpoints[lap_start_idx][0] if lap_start_idx < len(trackpoints) else 0,
                'end_epoch': trackpoints[lap_end_idx - 1][0] if lap_end_idx > lap_start_idx else 0,
                'distance_m': ldist, 'time_s': ltime,
                'avg_power': lap_avg_p, 'max_power': lap_max_p,
                'avg_hr': lap_avg_hr, 'max_hr': maxhr,
                'avg_cadence': cad, 'elevation_gain_m': None,
            })
            if ltime:
                total_time += ltime
            if ldist:
                pass  # DistanceMeters ist kumulativ pro Lap, oben über Trackpoints
        if not trackpoints:
            continue
        a.duration_s = total_time or (trackpoints[-1][0] or None)
        a.distance_m = total_dist or None
        a.elevation_gain_m = gain or None
        a.work_kj = work_j / 1000.0 if work_j else None
        powers = [p for (_, _, p, *_ ) in [(x[0], x[1], x[2]) for x in trackpoints] if False]
        pw = [tp[2] for tp in trackpoints]
        hs = [tp[3] for tp in trackpoints]
        cds = [tp[4] for tp in trackpoints]
        sps = [tp[5] for tp in trackpoints]
        a.power = pw
        a.hr = [int(h) if h is not None else None for h in hs]
        a.cadence = cds
        a.speed = sps
        a.lat = [tp[6] for tp in trackpoints]
        a.lon = [tp[7] for tp in trackpoints]
        a.alt = [tp[8] for tp in trackpoints]
        a.t = [tp[0] for tp in trackpoints]
        a.iso = [tp[1].isoformat() if tp[1] else '' for tp in trackpoints]
        real_pw = [p for p in pw if p is not None]
        real_hs = [h for h in hs if h is not None]
        real_cd = [c for c in cds if c is not None]
        a.avg_power = sum(real_pw) / len(real_pw) if real_pw else None
        a.max_power = max(real_pw) if real_pw else None
        a.avg_hr = sum(real_hs) / len(real_hs) if real_hs else None
        a.max_hr = max(real_hs) if real_hs else None
        a.avg_cadence = sum(real_cd) / len(real_cd) if real_cd else None
        real_sp = [s for s in sps if s is not None]
        a.avg_speed = sum(real_sp) / len(real_sp) if real_sp else None
        creator = root.find('.//{*}Creator')
        if creator is not None:
            name_el = creator.find('{*}Name')
            a.device_info = (name_el.text if name_el is not None else creator.attrib.get('Name')) or creator.attrib.get('Name')
        a.name = f"Ride {a.start_time.strftime('%Y-%m-%d')}" if a.start_time else "Unbenannte Fahrt"
        out.append(a)
    return out


def _txt(el, path: str, ns: dict):
    node = el.find(path, ns)
    if node is None:
        # ohne Namespace versuchen
        simple = path.split('/')[-1]
        node = el.find(f'.//{{}}{simple}') or el.find(simple)
        if node is None and '/' in path:
            # Pfad ohne NS
            parts = path.split('/')
            cur = el
            ok = True
            for p in parts:
                cur = cur.find(p.split(':')[-1])
                if cur is None:
                    ok = False
                    break
            node = cur if ok else None
    if node is not None and node.text:
        return node.text
    return None


def _gpx_latlon(tp, ns):
    lat = lon = None
    for tag in ('tc:Position/lat', 'Position/lat'):
        pass
    pos = tp.find('tc:Position', ns) or tp.find('Position')
    if pos is not None:
        la = pos.find('tc:LatitudeDegrees', ns) or pos.find('LatitudeDegrees')
        lo = pos.find('tc:LongitudeDegrees', ns) or pos.find('LongitudeDegrees')
        lat = _f(la.text) if la is not None and la.text else None
        lon = _f(lo.text) if lo is not None and lo.text else None
    return lat, lon


# ---------------------------------------------------------------------------
#  GPX
# ---------------------------------------------------------------------------

def parse_gpx(data: bytes) -> list[ActivityData]:
    root = ET.fromstring(data)
    # Namespace dynamisch ermitteln
    m = re.match(r'\{([^}]*)\}', root.tag)
    gpns = {'g': m.group(1)} if m else {}

    def find(el, path):
        if gpns:
            return el.find(path, gpns)
        return el.find(path.split('/')[-1])

    def findall(el, path):
        if gpns:
            return el.findall(path, gpns)
        return el.findall(path.split('/')[-1])

    trk_list = findall(root, 'g:trk') or findall(root, 'trk')
    out: list[ActivityData] = []
    for trk in trk_list:
        a = ActivityData()
        name_el = find(trk, 'g:name') or find(trk, 'name')
        a.name = name_el.text.strip() if name_el is not None and name_el.text else None
        points: list[tuple] = []
        for pt in trk.iter():
            tag = pt.tag.split('}')[-1]
            if tag != 'trkpt':
                continue
            lat = _f(pt.attrib.get('lat'))
            lon = _f(pt.attrib.get('lon'))
            ele = None
            tim = None
            ext: dict = {}
            for child in pt:
                ctag = child.tag.split('}')[-1]
                if ctag == 'ele':
                    ele = _f(child.text)
                elif ctag == 'time':
                    tim = _parse_iso(child.text)
                elif ctag in ('extensions', 'Extensions'):
                    for gchild in child.iter():
                        gtag = gchild.tag.split('}')[-1]
                        if gtag in ('power', 'watts', 'PWR'):
                            ext['power'] = _f(gchild.text)
                        elif gtag in ('hr', 'heartrate', 'bpm'):
                            ext['hr'] = _f(gchild.text)
                        elif gtag in ('cad', 'cadence'):
                            ext['cadence'] = _f(gchild.text)
                        elif gtag in ('speed', 'Spd'):
                            ext['speed'] = _f(gchild.text)
                        elif gtag in ('atemp', 'temperature'):
                            ext['temp'] = _f(gchild.text)
            if tim and a.start_time is None:
                a.start_time = tim
            tsec = (tim - a.start_time).total_seconds() if (tim and a.start_time) else len(points)
            points.append((tsec, tim, ext.get('power'), ext.get('hr'),
                           ext.get('cadence'), ext.get('speed'), lat, lon, ele,
                           ext.get('temp')))
        if not points:
            continue
        a.t = [p[0] for p in points]
        a.iso = [p[1].isoformat() if p[1] else '' for p in points]
        a.power = [p[2] for p in points]
        a.hr = [int(p[3]) if p[3] is not None else None for p in points]
        a.cadence = [p[4] for p in points]
        a.speed = [p[5] for p in points]
        a.lat = [p[6] for p in points]
        a.lon = [p[7] for p in points]
        a.alt = [p[8] for p in points]
        a.temp = [p[9] for p in points]
        # Distanz über Haversine
        dist = 0.0
        gain = 0.0
        last = None
        last_alt = None
        import math
        for p in points:
            lat, lon = p[6], p[7]
            if lat is not None and lon is not None:
                if last is not None:
                    dist += _haversine(last[0], last[1], lat, lon)
                last = (lat, lon)
            if p[8] is not None:
                if last_alt is not None and p[8] > last_alt:
                    gain += p[8] - last_alt
                last_alt = p[8]
        a.distance_m = dist or None
        a.elevation_gain_m = gain or None
        if points[-1][0]:
            a.duration_s = points[-1][0]
        pw = [p for p in a.power if p is not None]
        hs = [p for p in a.hr if p is not None]
        cd = [p for p in a.cadence if p is not None]
        a.avg_power = sum(pw) / len(pw) if pw else None
        a.max_power = max(pw) if pw else None
        a.avg_hr = sum(hs) / len(hs) if hs else None
        a.max_hr = max(hs) if hs else None
        a.avg_cadence = sum(cd) / len(cd) if cd else None
        if pw:
            a.work_kj = sum(pw) * (a.duration_s / len(pw)) / 1000.0 if a.duration_s else None
        meta = find(trk, 'g:metadata') or find(trk, 'metadata')
        if meta is not None:
            auth = find(meta, 'g:author/g:name') or find(meta, 'author/name')
            if auth is not None and auth.text:
                a.device_info = auth.text.strip()
        if a.name is None:
            a.name = f"Fahrt {a.start_time.strftime('%Y-%m-%d')}" if a.start_time else "Unbenannte Fahrt"
        out.append(a)
    return out


def _haversine(lat1, lon1, lat2, lon2) -> float:
    import math
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
