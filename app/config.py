"""Anwendungskonfiguration.

Alle Werte kommen aus Umgebungsvariablen — Zugangsdaten stehen nie im Quellcode.
Wichtige Variable:
  CYCLING_SECRET_KEY : Pflicht. Wird z.B. generiert mit:
      python3 -c "import secrets; print(secrets.token_hex(32))"
  CYCLING_DB_PATH    : Pfad zur SQLite-Datenbank (Default: data/cycling.db)
  CYCLING_UPLOAD_DIR : Verzeichnis für Original-Importdateien (Default: data/uploads)
  CYCLING_PORT       : Port der Anwendung (Default: 8080, Apache bleibt auf 80)
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.environ.get("CYCLING_DB_PATH", str(BASE_DIR / "data" / "cycling.db")))
UPLOAD_DIR = Path(os.environ.get("CYCLING_UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")))
SECRET_KEY = os.environ.get("CYCLING_SECRET_KEY", "")
PORT = int(os.environ.get("CYCLING_PORT", "8080"))

# Upload-Grenzen (Ressourcen: 4 GB RAM Server -> bewusst klein gehalten)
MAX_FILE_BYTES = int(os.environ.get("CYCLING_MAX_FILE_BYTES", str(64 * 1024 * 1024)))   # 64 MB pro Datei
MAX_ZIP_ENTRIES = 5000          # gegen ZIP-Bomben (Anzahl Einträge)
MAX_ZIP_UNCOMPRESSED = 256 * 1024 * 1024  # 256 MB entpackt gesamt pro ZIP
MAX_ZIP_ENTRY_SIZE = 64 * 1024 * 1024     # 64 MB pro Eintrag

SESSION_COOKIE = "cpc_session"
SESSION_MAX_AGE_S = 60 * 60 * 24 * 30  # 30 Tage

def ensure_dirs() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

STATIC_DIR = Path(os.environ.get("CYCLING_STATIC_DIR", str(BASE_DIR / "static")))
COOKIE_SECURE = os.environ.get("CYCLING_COOKIE_SECURE", "0") == "1"

# Standard-Leistungszonen (5 Zonen, %FTP). Überschreibbar per Settings.
POWER_ZONES = [
    {"no": 1, "name": "Zone 1 Erholung", "low": 0, "high": 55},
    {"no": 2, "name": "Zone 2 Grundlagenausdauer", "low": 55, "high": 75},
    {"no": 3, "name": "Zone 3 Tempo", "low": 75, "high": 90},
    {"no": 4, "name": "Zone 4 Schwelle", "low": 90, "high": 105},
    {"no": 5, "name": "Zone 5 VO2max/Sprint", "low": 105, "high": 300},
]
