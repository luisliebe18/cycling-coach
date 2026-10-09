"""SQLite-Datenbankschicht mit versionierten Migrationen.

- WAL-Modus für robuste Lese-/Schreiboperationen.
- Migrationen sind numeriert, aufwärts und zerstörungsfrei (nur ADD/CREATE, kein DROP von Daten).
- Fremdschlüssel aktiviert.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, str] = {
    1: """
-- ================= Aktivitäten =================
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,                    -- ursprüngliche Aktivitätskennung (FIT session_id / Datei-Stamm)
    source TEXT NOT NULL DEFAULT 'import',      -- 'import' | 'manual'
    filename TEXT,                      -- gespeicherter Originaldateiname (relativ zu UPLOAD_DIR)
    file_sha256 TEXT,                   -- Hash der Originaldatei (Duplikat-/Integritätsprüfung)
    name TEXT NOT NULL,
    activity_type TEXT NOT NULL DEFAULT 'Ride',
    sport TEXT,                          -- z.B. cycling, running (aus FIT)
    start_time TEXT NOT NULL,           -- ISO 8601 UTC
    end_time TEXT,
    duration_s REAL,                    -- Gesamtzeit (elapsed)
    moving_time_s REAL,                 -- Bewegungsdauer
    distance_m REAL,
    elevation_gain_m REAL,
    avg_speed REAL,
    max_speed REAL,
    avg_power REAL,
    np_power REAL,                      -- Normalized Power (30s-Fenster)
    max_power REAL,
    work_kj REAL,
    avg_hr REAL,
    max_hr REAL,
    avg_cadence REAL,
    max_cadence REAL,
    hr_data INTEGER DEFAULT 0,          -- Messreihe HR vorhanden
    power_data INTEGER DEFAULT 0,       -- Messreihe Power vorhanden
    cadence_data INTEGER DEFAULT 0,
    gps_data INTEGER DEFAULT 0,
    has_gps INTEGER DEFAULT 0,
    indoor INTEGER,                     -- 0/1 wenn erkennbar, NULL sonst
    temperature_c REAL,
    device_info TEXT,
    if_value REAL,                      -- Intensitätsfaktor (berechnet, FTP zum Importzeitpunkt)
    tss REAL,                           -- Trainingsbelastung (berechnet)
    rpe INTEGER,                        -- subjektive Belastung 1..10 (Journal)
    notes TEXT,                         -- Journal-Notiz
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_act_start ON activities(start_time);
CREATE INDEX IF NOT EXISTS idx_act_uid ON activities(uid);
CREATE INDEX IF NOT EXISTS idx_act_sha ON activities(file_sha256);

-- ================= Zeitreihen (Messreihen) =================
-- Ein Satz pro Aktivität; Werte als JSON-Listen (kompakt, ein Blob pro Spalte).
-- epoch: Sekunden seit Activity-Start (float), time: ISO-Zeitstempel.
CREATE TABLE IF NOT EXISTS series (
    activity_id INTEGER PRIMARY KEY REFERENCES activities(id) ON DELETE CASCADE,
    n INTEGER NOT NULL,
    epoch TEXT NOT NULL,     -- JSON [s]
    time TEXT NOT NULL,      -- JSON [iso]
    power TEXT,              -- JSON [w| null]
    hr TEXT,                 -- JSON [bpm|null]
    cadence TEXT,            -- JSON [rpm|null]
    speed TEXT,              -- JSON [m/s|null]
    lat TEXT,                -- JSON [deg|null]
    lon TEXT,                -- JSON [deg|null]
    alt TEXT,                -- JSON [m|null]
    temp TEXT                -- JSON [°C|null]
);

-- ================= Runden / Intervalle =================
CREATE TABLE IF NOT EXISTS laps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    lap_no INTEGER NOT NULL,
    start_epoch REAL,
    end_epoch REAL,
    distance_m REAL,
    time_s REAL,
    avg_power REAL,
    max_power REAL,
    avg_hr REAL,
    max_hr REAL,
    avg_cadence REAL,
    avg_speed REAL,
    elevation_gain_m REAL
);
CREATE INDEX IF NOT EXISTS idx_lap_act ON laps(activity_id);

-- Automatisch erkannte Belastungsabschnitte
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,        -- 'work' | 'rest'
    start_epoch REAL,
    end_epoch REAL,
    avg_power REAL,
    max_power REAL,
    avg_hr REAL,
    length_s REAL
);
CREATE INDEX IF NOT EXISTS idx_seg_act ON segments(activity_id);

-- ================= Maximale mittlere Leistungen (Power Curve) =================
CREATE TABLE IF NOT EXISTS mmp_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    duration_s INTEGER NOT NULL,
    watts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mmp_act ON mmp_entries(activity_id);
CREATE INDEX IF NOT EXISTS idx_mmp_dur ON mmp_entries(duration_s, watts);

-- ================= Bestleistungen (PB) =================
CREATE TABLE IF NOT EXISTS best_efforts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    duration_s INTEGER NOT NULL,
    activity_id INTEGER REFERENCES activities(id) ON DELETE SET NULL,
    watts REAL NOT NULL,
    wkg REAL,
    achieved_on TEXT,
    UNIQUE(duration_s)
);

-- ================= Profileinstellungen historisch =================
CREATE TABLE IF NOT EXISTS settings_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,             -- 'ftp' | 'weight' | ...
    value REAL NOT NULL,
    effective_from TEXT NOT NULL,  -- ISO Datum/Uhrzeit
    source TEXT NOT NULL DEFAULT 'manual',   -- manual | estimate | test
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_setshist ON settings_history(key, effective_from);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Zonen (5 Zonen, Grenzwerte als %FTP gespeichert oder absolut)
CREATE TABLE IF NOT EXISTS zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_no INTEGER NOT NULL,
    name TEXT NOT NULL,
    low_pct REAL, high_pct REAL,
    low_abs REAL, high_abs REAL,
    UNIQUE(zone_no)
);

-- ================= Coaching =================
CREATE TABLE IF NOT EXISTS checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL UNIQUE,        -- YYYY-MM-DD lokal
    sleep_hours REAL,
    sleep_quality INTEGER,           -- 1..5
    fatigue INTEGER,                 -- 1..5 (5 = sehr müde)
    soreness INTEGER,                -- 1..5
    motivation INTEGER,              -- 1..5
    wellness INTEGER,                -- 1..5
    symptoms TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL,        -- YYYY-MM-DD (Montag)
    payload TEXT NOT NULL,           -- JSON der Woche inkl. Begründungen
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,               -- geplantes Datum YYYY-MM-DD
    title TEXT NOT NULL,
    goal TEXT,
    duration_min INTEGER,
    intensity_zone TEXT,
    structure TEXT,                  -- JSON Blöcke
    est_tss REAL,
    status TEXT NOT NULL DEFAULT 'planned',  -- planned | done | skipped
    activity_id INTEGER REFERENCES activities(id) ON DELETE SET NULL,
    reason TEXT,                     -- Begründung des Coaches
    UNIQUE(day, title)
);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    payload TEXT NOT NULL,           -- JSON Empfehlung inkl. Begründung
    created_at TEXT DEFAULT (datetime('now'))
);

-- ================= Import-Protokoll =================
CREATE TABLE IF NOT EXISTS import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    filename TEXT,
    status TEXT NOT NULL,            -- ok | duplicate | error | skipped
    message TEXT,
    activity_id INTEGER
);

-- ================= Strecken / Segmente (Phase Zusatz) =================
CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    bbox TEXT,                       -- JSON minlat,minlon,maxlat,maxlon
    points_json TEXT,                -- reduzierter Track für Vergleich
    distance_m REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS route_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id INTEGER REFERENCES routes(id) ON DELETE CASCADE,
    activity_id INTEGER REFERENCES activities(id) ON DELETE CASCADE,
    score REAL,
    UNIQUE(route_id, activity_id)
);

-- ================= Ausrüstung =================
CREATE TABLE IF NOT EXISTS bikes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    components TEXT,
    km_total REAL DEFAULT 0,
    notes TEXT
);

-- ================= Trainingsjournal pro Aktivität =================
CREATE TABLE IF NOT EXISTS journals (
    activity_id INTEGER PRIMARY KEY REFERENCES activities(id) ON DELETE CASCADE,
    notes TEXT,
    rpe INTEGER,
    conditions TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ================= Nutzer (single-user Bootstrap) =================
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- ================= KI-Anbindung (optional, Schlüssel nur per ENV) =================
CREATE TABLE IF NOT EXISTS coach_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    role TEXT NOT NULL,              -- user | assistant
    content TEXT NOT NULL
);
""",
}


def _conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    p = str(db_path or config.DB_PATH)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return con


def get_db() -> sqlite3.Connection:
    return _conn()


# Alias, damit Router/Abhängigkeiten beides verwenden können
get_conn = get_db


def backup_database(db_path=None):
    """Sichere Kopie der DB vor destruktiven Aktionen."""
    import datetime as _dt
    import shutil
    src = str(db_path or config.DB_PATH)
    dst = Path(src).with_suffix(f".backup-{_dt.datetime.now():%Y%m%d-%H%M%S}.db")
    con = _conn(src)
    bcon = _conn(dst)
    con.backup(bcon)
    bcon.close(); con.close()
    return dst


def migrate(con: sqlite3.Connection) -> int:
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = con.execute("SELECT MAX(version) v FROM schema_version").fetchone()
    cur = row["v"] or 0
    for v in sorted(MIGRATIONS):
        if v > cur:
            con.executescript(MIGRATIONS[v])
            con.execute("INSERT INTO schema_version(version) VALUES (?)", (v,))
    _ensure_columns(con)
    con.commit()
    row = con.execute("SELECT MAX(version) v FROM schema_version").fetchone()
    return row["v"] or 0


# Spalten, die Code/Parserschnittstelle erwartet; werden bei Bedarf ergänzt
# (nicht-destruktive ADD-COLUMN-Migration, bestehende Daten bleiben erhalten).
_ADDED_COLUMNS = [
    ("activities", "end_time", "TEXT"),
    ("activities", "max_cadence", "REAL"),
    ("activities", "gps_data", "INTEGER DEFAULT 0"),
    ("activities", "temperature_c", "REAL"),
    ("activities", "rpe", "INTEGER"),
    ("activities", "notes", "TEXT"),
]


def _ensure_columns(con: sqlite3.Connection) -> None:
    existing = {r["name"] for r in con.execute("PRAGMA table_info(activities)")}
    for table, col, typ in _ADDED_COLUMNS:
        if col not in existing:
            try:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # bereits vorhanden / Renn-Condition toleriert


def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    config.ensure_dirs()
    con = _conn(db_path)
    migrate(con)
    _seed_defaults(con)
    return con


def _seed_defaults(con: sqlite3.Connection) -> None:
    if not con.execute("SELECT 1 FROM zones LIMIT 1").fetchone():
        zones = [
            (1, "Zone 1 – Erholung", 0, 55),
            (2, "Zone 2 – Grundlagenausdauer", 55, 75),
            (3, "Zone 3 – Tempo", 75, 90),
            (4, "Zone 4 – Schwelle", 90, 105),
            (5, "Zone 5 – VO2max/Sprint", 105, 300),
        ]
        con.executemany(
            "INSERT INTO zones(zone_no,name,low_pct,high_pct) VALUES (?,?,?,?)", zones
        )
        con.commit()
