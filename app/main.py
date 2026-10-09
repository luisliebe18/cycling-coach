"""FastAPI-Hauptanwendung: App, Router, statisches Frontend."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .api import router as api_router

config.ensure_dirs()
db.init_db()

app = FastAPI(title="Personal Cycling Performance Center", version="1.0")
app.include_router(api_router, prefix="/api")


@app.get('/healthz')
def healthz():
    return {'ok': True}


@app.get('/')
def index():
    return FileResponse(config.STATIC_DIR / 'index.html')


app.mount('/static', StaticFiles(directory=config.STATIC_DIR), name='static')
