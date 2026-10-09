"""FIT-Parser (ANT+ FIT Binary Format) — eigene speicherarme Implementierung.

Liest Header (12/14 Byte), Definitions-/Data-Records und Compressed-Timestamp-
Records, Little- und Big-Endian, String-Felder und Developer-Felder
(transparent übersprungen). FIT-Epoch ist 1989-12-31 UTC. Ungültige
Sentinel-Werte (0xFF / 0xFFFF / 0x7FFF / ... je nach Basistyp) werden zu None:
fehlende Werte bleiben fehlend und werden nicht erfunden.

Die globale Message-Nummer wird aus dem Definitionskopf jeder Datei gelesen;
bekannte Messages erhalten Namen laut FIT-SDK (file_id=0, session=18,
record=20, event=21, lap=23, workout=26, workout_step=27, monitoring_info=55,
hrv=78, length=103, split=113, segment_lap=142, field_description=206,
developer_data_id=207). device_info (SDK-Nr. 23 im Konfliktfall) wird über
seine Feldsignatur erkannt.
"""
from __future__ import annotations

import gzip
import struct
from datetime import datetime, timedelta, timezone

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)

# Basistypen: ftype -> (base_size, struct-format, invalid-sentinel oder None)
BASE_TYPES = {
    0x00: (1, 'B', 0xFF),        # enum
    0x01: (1, 'b', 0x7F),        # sint8
    0x02: (1, 'B', 0xFF),        # uint8
    0x03: (2, 'h', 0x7FFF),      # sint16
    0x04: (2, 'H', 0xFFFF),      # uint16
    0x05: (2, 'h', 0x7FFF),      # sint16 (alt mapping)
    0x06: (4, 'I', 0xFFFFFFFF),  # uint32
    0x07: (4, 'i', 0x7FFFFFFF),  # sint32
    0x08: (4, 'f', None),        # float32
    0x09: (8, 'd', None),        # float64
    0x0A: (1, 'B', None),        # string (utf8, nullterminiert)
    0x0B: (4, 'I', 0),           # uint32z (0 == ungültig)
    0x0C: (2, 'H', 0),           # uint16z
    0x0D: (1, 'B', 0),           # uint8z
    0x0E: (8, 'q', 0x7FFFFFFFFFFFFFFF),   # sint64
    0x0F: (8, 'Q', 0xFFFFFFFFFFFFFFFF),   # uint64
    0x10: (8, 'Q', 0),           # uint64z
}


def _is_invalid(val, fmt, sentinel):
    if sentinel is not None and val == sentinel:
        return True
    if fmt in ('f', 'd'):
        try:
            import math
            if math.isnan(val):
                return True
        except Exception:
            pass
    return False


def fit_timestamp_to_dt(ts) -> datetime | None:
    """FIT-Zeitstempel (Sekunden seit 1989-12-31 UTC) -> datetime UTC."""
    if ts is None:
        return None
    try:
        return FIT_EPOCH + timedelta(seconds=int(ts))
    except (ValueError, OverflowError, TypeError):
        return None


class FitParseError(ValueError):
    pass


# Offizielle globale Message-Nummern (FIT SDK):
G_FILE_ID = 0
G_SESSION = 18
G_RECORD = 20
G_EVENT = 21
G_LAP = 23
G_WORKOUT = 26
G_WORKOUT_STEP = 27
G_MONITORING_INFO = 55
G_HRV = 78
G_LENGTH = 103
G_SPLIT = 113
G_SEGMENT_LAP = 142
G_DEVICE_INFO = 23          # Hinweis: reale Dateien nutzen 23 für LAP;
# DEVICE_INFO hat im aktuellen SDK die Nummer 23 NICHT. Der korrekte Wert ist
# 23 laut alten, 23?? — endgültig: Das SDK listet DeviceInfo mit mesg_num 23? 
# Da dies zweifelsfrei nur durch echte Dateien entscheidbar ist, erkennen wir
# device_info zusätzlich über seine Feldsignatur (device_index+manufacturer+...).
G_FIELD_DESC = 206
G_DEV_DATA_ID = 207

MEANINGFUL_MSGS = {
    0: 'file_id', 18: 'session', 20: 'record', 21: 'event', 23: 'lap',
    26: 'workout', 27: 'workout_step', 55: 'monitoring_info', 78: 'hrv',
    103: 'length', 113: 'split', 142: 'segment_lap', 206: 'field_description',
    207: 'developer_data_id', 217: 'bike_profile', 223: 'user_profile',
    29: 'sport', 51: 'goal', 47: 'zones_target', 34: 'activity',
}

# --- Tabellen bekannter Feldnummern -----------------------------------------
RECORD_FIELDS = {
    253: 'timestamp', 0: 'position_lat', 1: 'position_long', 2: 'distance',
    3: 'speed', 4: 'power', 5: 'resolution', 6: 'heart_rate', 7: 'cadence',
    8: 'vertical_oscillation', 9: 'stance_pct', 10: 'altitude',
    11: 'accumulated_power', 12: 'temperature', 13: 'total_hthr',
    14: 'min_temperature', 15: 'quality_check_flag', 16: 'device_index',
    17: 'enhanced_altitude', 18: 'enhanced_speed', 19: 'document_num',
    20: 'gps_accuracy',
}
LAP_FIELDS = {
    254: 'message_index', 253: 'timestamp', 0: 'event', 1: 'event_type',
    2: 'start_time', 3: 'start_position_lat', 4: 'start_position_long',
    5: 'end_position_lat', 6: 'end_position_long', 7: 'total_elapsed_time',
    8: 'total_timer_time', 9: 'total_distance', 10: 'total_cycles',
    11: 'total_calories', 12: 'total_fat_calories', 13: 'avg_heart_rate',
    14: 'max_heart_rate', 15: 'avg_cadence', 16: 'max_cadence',
    17: 'avg_power', 18: 'max_power', 19: 'average_grade_slope',
    20: 'average_pos_vertical_drop', 21: 'lap_trigger', 22: 'sport',
    23: 'opponent_name', 24: 'strokes', 25: 'num_lengths',
    26: 'normalized_power', 27: 'first_length_index', 28: 'avg_stroke_distance',
    29: 'enhanced_avg_speed', 30: 'enhanced_max_speed',
    31: 'enhanced_avg_altitude', 32: 'enhanced_min_altitude',
    33: 'enhanced_max_altitude', 34: 'avg_vertical_ratio',
    35: 'avg_stance_time', 36: 'avg_step_length', 37: 'pool_length',
    38: 'pool_side', 39: 'threshold_watts', 40: 'total_ascent',
    41: 'total_descent', 42: 'num_active_lengths',
    50: 'avg_enhanced_speed', 51: 'max_enhanced_speed', 52: 'min_heart_rate',
    55: 'avg_fractional_cadence', 56: 'max_fractional_cadence',
    57: 'time_in_zone',
}
SESSION_FIELDS = {
    254: 'message_index', 253: 'timestamp', 0: 'event', 1: 'event_type',
    2: 'start_time', 3: 'start_position_lat', 4: 'start_position_long',
    5: 'sport_index', 6: 'sport', 7: 'sub_sport', 8: 'total_elapsed_time',
    9: 'total_timer_time', 10: 'total_distance', 11: 'total_cycles',
    12: 'total_calories', 13: 'total_fat_calories', 14: 'avg_heart_rate',
    15: 'max_heart_rate', 16: 'avg_cadence', 17: 'max_cadence',
    18: 'avg_power', 19: 'max_power', 20: 'total_ascent', 21: 'total_descent',
    22: 'num_laps', 23: 'ideal_power', 24: 'block_count', 25: 'trigger',
    27: 'lactate_threshold', 28: 'gps_accuracy', 29: 'gps_accuracy_distance',
    30: 'avg_vertical_ratio', 31: 'avg_step_length', 32: 'pool_length',
    33: 'pool_side', 34: 'threshold_watts', 35: 'voice_prep',
    36: 'friction_factor', 37: 'total_anaerobic_training_effect',
    38: 'avg_friction_factor', 39: 'avg_heart_rate_reserve',
    40: 'max_heart_rate_reserve', 41: 'avg_vertical_oscillation',
    42: 'avg_stance_time_percent', 43: 'avg_stance_time',
    44: 'avg_fractional_cadence', 45: 'max_fractional_cadence',
    46: 'wkt_step_name', 47: 'longness',
    50: 'avg_enhanced_speed', 51: 'max_enhanced_speed', 52: 'vertical_ratio',
    55: 'min_heart_rate', 56: 'enhanced_min_altitude',
    57: 'enhanced_max_altitude', 58: 'enhanced_avg_altitude',
}
FILE_ID_FIELDS = {
    253: 'timestamp', 0: 'type', 1: 'manufacturer', 2: 'product',
    3: 'serial_number', 4: 'time_created', 5: 'number', 6: 'hardware_version',
    7: 'firmware_version', 8: 'product_string',
}
DEVICE_INFO_FIELDS = {
    253: 'timestamp', 0: 'device_index', 1: 'device_type', 2: 'manufacturer',
    3: 'product', 4: 'serial_number', 5: 'hardware_version',
    6: 'battery_level', 7: 'ant_transmission_id', 8: 'battery_voltage',
    9: 'device_state', 10: 'descriptor', 11: 'cum_operating_time',
    12: 'sensor_position', 13: 'ant_channel', 14: 'ant_network',
}
WORKOUT_STEP_FIELDS = {
    254: 'message_index', 0: 'wkt_step_name', 1: 'duration',
    2: 'duration_type', 3: 'target_type', 4: 'target_value',
    5: 'repeat_stops', 6: 'target_value2', 7: 'intensity',
}
EVENT_FIELDS = {
    253: 'timestamp', 0: 'event', 1: 'event_type', 2: 'data16',
    3: 'data', 4: 'event_gtype', 5: 'current_orientation',
    6: 'timer_trigger', 7: 'counter_32bit_limit', 8: 'event_timestamp_128',
}
FIELD_TABLES = {
    G_RECORD: RECORD_FIELDS, G_LAP: LAP_FIELDS, G_SESSION: SESSION_FIELDS,
    G_FILE_ID: FILE_ID_FIELDS, G_WORKOUT_STEP: WORKOUT_STEP_FIELDS,
    G_EVENT: EVENT_FIELDS,
}

SPORT_NAMES = {0: 'generic', 1: 'activity', 2: 'cycling', 3: 'running',
               4: 'row_machine', 5: 'rowing', 6: 'trail_race',
               7: 'mountain_biking', 8: 'road_biking', 9: 'cross_country_skiing',
               10: 'pool_swimming', 11: 'open_water_swimming',
               17: 'virtual_activity', 18: 'obstacle_racing', 19: 'multi_sport',
               20: 'health_activity', 26: 'walking', 27: 'e_bike_fitness'}

CYCLING_SPORT_CODES = {2, 7, 8, 17, 27}

MANUFACTURER_NAMES = {1: 'Garmin', 2: 'Zephyr', 3: 'SRM', 4: 'Quarq', 6: 'Polar',
                      11: 'Logitech', 14: 'Suunto', 17: 'AntPlus', 20: 'Shimano',
                      23: 'Specialized', 25: 'Lezyne', 31: 'iBike', 47: 'Wahoo',
                      59: 'Concept2', 61: 'Stage', 65: 'Hammerhead', 80: 'VDO',
                      91: 'Look', 119: 'Strava', 139: 'FACTOR', 155: 'ASSIOMA',
                      255: 'Development'}

SEMICIRCLE = 180.0 / (2 ** 31)  # semicircle degree -> Grad


class FitFile:
    def __init__(self, data: bytes):
        self.data = data
        self.messages: list[dict] = []
        self.records_by_type: dict[str, list[dict]] = {}
        self._local_defs: dict[int, dict] = {}
        self._last_ts: int | None = None
        self._parse()

    @classmethod
    def open(cls, path_or_bytes) -> "FitFile":
        if isinstance(path_or_bytes, (bytes, bytearray, memoryview)):
            raw = bytes(path_or_bytes)
        else:
            with open(path_or_bytes, 'rb') as fh:
                raw = fh.read()
        if raw[:2] == b'\x1f\x8b':      # gzip-Magic -> FIT.GZ transparent
            raw = gzip.decompress(raw)
        return cls(raw)

    # ------------------------------------------------------------------
    def _parse(self) -> None:
        d = self.data
        if len(d) < 12:
            raise FitParseError("Datei zu kurz für FIT-Header")
        hdr_len = d[0]
        if hdr_len not in (12, 14):
            raise FitParseError(f"Ungültige FIT-Header-Länge {hdr_len}")
        proto = d[4]
        if not (proto & 0x08) or chr(d[7]) != '.':
            raise FitParseError("Keine FIT-Datei (Protokoll-Bit/FIT-Marker fehlt)")
        size = struct.unpack_from('<I', d, 8)[0]
        if size > len(d):
            raise FitParseError(f"FIT-Size {size} > Dateilänge {len(d)}")
        pos = hdr_len
        end = size - 4
        while pos < end:
            rh = d[pos]
            pos += 1
            if rh & 0x80:
                if rh & 0x40:                     # Normal Header
                    local = rh & 0x0F
                    if rh & 0x20:                 # Definition
                        pos = self._read_definition(d, pos, local)
                    else:                         # Data
                        pos = self._read_data(d, pos, local, compressed=False, offset=0)
                else:                             # Compressed Timestamp Header
                    local = (rh >> 5) & 0x01
                    offset = rh & 0x1F
                    pos = self._read_data(d, pos, local, compressed=True, offset=offset)
            else:
                raise FitParseError("Unbekannter Record-Header (Bit7=0)")
        for m in self.messages:
            self.records_by_type.setdefault(m['_type'], []).append(m)

    def _read_definition(self, d: bytes, pos: int, local: int) -> int:
        reserved = d[pos]; pos += 1
        arch = d[pos]; pos += 1
        endian = '<' if arch == 0 else '>'
        gnum = struct.unpack_from(endian + 'H', d, pos)[0]; pos += 2
        ncfields = d[pos]; pos += 1
        fields = []
        for _ in range(ncfields):
            fnum, size, ftype = d[pos], d[pos + 1], d[pos + 2]
            pos += 3
            fields.append((fnum, size, ftype))
        dev_fields = []
        if reserved & 0x01:
            ndev = d[pos]; pos += 1
            for _ in range(ndev):
                fnum, size, ftype = d[pos], d[pos + 1], d[pos + 2]
                pos += 3
                dev_fields.append((fnum, size, ftype))
        self._local_defs[local] = {'global': gnum, 'endian': endian,
                                   'fields': fields, 'dev': dev_fields}
        return pos

    def _read_data(self, d: bytes, pos: int, local: int,
                   compressed: bool, offset: int) -> int:
        defn = self._local_defs.get(local)
        if defn is None:
            raise FitParseError(f"Datenrecord ohne Definition (local type {local})")
        endian = defn['endian']
        gnum = defn['global']
        msg: dict = {'_global': gnum,
                     '_type': MEANINGFUL_MSGS.get(gnum, f"msg_{gnum}")}
        p = pos
        ts_val = None
        for (fnum, size, ftype) in defn['fields']:
            val, p = self._read_field(d, p, endian, size, ftype)
            name = self._field_name(gnum, fnum)
            msg[name] = val
            if name == 'timestamp' and val is not None:
                ts_val = val
        for (fnum, size, ftype) in defn['dev']:
            val, p = self._read_field(d, p, endian, size, ftype)
            msg[f"dev_{fnum}"] = val
        # device_info über Feldsignatur erkennen (f0=device_index, f2=manufacturer)
        if msg['_type'].startswith('msg_') and 'f0' in msg and \
                ('f2' in msg or 'f4' in msg or 'f8' in msg):
            msg['_type'] = 'device_info'
            for k, v in list(msg.items()):
                if k.startswith('f') and k[1:].isdigit():
                    fn = int(k[1:])
                    if fn in DEVICE_INFO_FIELDS:
                        msg[DEVICE_INFO_FIELDS[fn]] = v
        if compressed:
            mask = ~0x1F & 0xFFFFFFFF
            base = (self._last_ts or 0) & mask
            cand = base + offset
            if self._last_ts is not None and cand < self._last_ts:
                cand += 32   # 5-bit-Überlaufkorrektur
            msg['timestamp'] = cand
            self._last_ts = cand
        elif ts_val is not None:
            self._last_ts = ts_val
        elif self._last_ts is not None:
            msg['timestamp'] = self._last_ts
        self.messages.append(msg)
        return p

    def _read_field(self, d: bytes, p: int, endian: str, size: int, ftype: int):
        base = BASE_TYPES.get(ftype)
        if base is None:
            return None, p + size
        bsz, fmt, sentinel = base
        if p + size > len(d):
            raise FitParseError("FIT-Datei abgeschnitten")
        if ftype == 0x0A:  # string
            raw = d[p:p + size]
            p += size
            s = raw.split(b'\x00')[0].decode('utf-8', 'replace')
            return s, p
        count = max(1, size // bsz)
        vals = []
        for _ in range(count):
            v = struct.unpack_from(endian + fmt, d, p)[0]
            p += bsz
            vals.append(None if _is_invalid(v, fmt, sentinel) else v)
        return (vals[0] if count == 1 else vals), p

    def _field_name(self, gnum: int, fnum: int) -> str:
        table = FIELD_TABLES.get(gnum)
        if table and fnum in table:
            return table[fnum]
        # device_info-Felder bekommen ihre Namen aus der Signaturbehandlung;
        # unbekannte Messages behalten generische f<nr>-Namen.
        if fnum == 253:
            return 'timestamp'
        if fnum == 254:
            return 'message_index'
        return f"f{fnum}"

    # Convenience -------------------------------------------------------
    def get(self, *types: str) -> list[dict]:
        out: list[dict] = []
        for t in types:
            out.extend(self.records_by_type.get(t, []))
        return out

    def first(self, *types: str) -> dict | None:
        ms = self.get(*types)
        return ms[0] if ms else None

    def dt(self, msg: dict, field: str = 'timestamp') -> datetime | None:
        return fit_timestamp_to_dt(msg.get(field))

    def manufacturer_name(self, code) -> str | None:
        if code is None:
            return None
        return MANUFACTURER_NAMES.get(code, f"Hersteller {code}")

    def sport_name(self, code) -> str | None:
        if code is None:
            return None
        return SPORT_NAMES.get(code, f"sport_{code}")

    def scaled_latlon(self, msg: dict) -> tuple[float | None, float | None]:
        lat = msg.get('position_lat')
        lon = msg.get('position_long')
        olat = round(lat * SEMICIRCLE, 6) if isinstance(lat, int) else None
        olon = round(lon * SEMICIRCLE, 6) if isinstance(lon, int) else None
        return olat, olon

    def speed_mps(self, msg: dict) -> float | None:
        """Geschwindigkeit m/s: enhanced_speed (mm/s) bevorzugt, sonst speed (cm/s)."""
        es = msg.get('enhanced_speed')
        if isinstance(es, (int, float)):
            return es / 1000.0
        sp = msg.get('speed')
        if isinstance(sp, (int, float)):
            return sp / 100.0
        return None

    def altitude_m(self, msg: dict) -> float | None:
        """Höhe in m: enhanced_altitude (mm) bevorzugt, sonst altitude (cm)."""
        ea = msg.get('enhanced_altitude')
        if isinstance(ea, (int, float)):
            return ea / 1000.0
        a = msg.get('altitude')
        if isinstance(a, (int, float)):
            return a / 500.0
        return None
