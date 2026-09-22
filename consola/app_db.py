# -*- coding: utf-8 -*-
"""app_db.py — historial de chat de la CONSOLA (no toca la memoria de los agentes).

SQLite propio en consola/datos/consola.sqlite3. Tabla conversaciones.
Cada mensaje guarda el MODO en que se hizo ('agente' o 'humano') para que
el historial de cada modo sea independiente.
"""
import json
import os
import sqlite3
import time

RUTA = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(RUTA, 'datos')
DB = os.path.join(DATOS, 'consola.sqlite3')


def conectar():
    os.makedirs(DATOS, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS conversaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agente_id TEXT NOT NULL,
        rol TEXT NOT NULL,
        texto TEXT NOT NULL,
        modelo TEXT NOT NULL DEFAULT '',
        modo TEXT NOT NULL DEFAULT 'agente',
        ts REAL NOT NULL)""")
    cols = [r['name'] for r in c.execute("PRAGMA table_info(conversaciones)")]
    if 'modo' not in cols:
        c.execute("ALTER TABLE conversaciones ADD COLUMN modo TEXT NOT NULL DEFAULT 'agente'")
    if 'audio' not in cols:
        # Nombre de fichero de audio asociado (datos/voz/<aid>/<nombre>),
        # como los notas de voz de Telegram: reproducibles más tarde.
        c.execute("ALTER TABLE conversaciones ADD COLUMN audio TEXT NOT NULL DEFAULT ''")
    if 'trazado' not in cols:
        # Traza de lo que hizo el agente (herramientas + salidas, JSON) para el
        # desplegable "ejecutado" del chat. Persiste -> revisable cuando quieras.
        c.execute("ALTER TABLE conversaciones ADD COLUMN trazado TEXT NOT NULL DEFAULT ''")
    c.execute("CREATE INDEX IF NOT EXISTS idx_conv ON conversaciones(agente_id, id)")
    return c


def db_guardar(aid, rol, texto, modelo='', modo='agente', audio='', trazado=''):
    if not isinstance(trazado, str):
        trazado = json.dumps(trazado, ensure_ascii=False)
    with conectar() as c:
        c.execute(
            "INSERT INTO conversaciones(agente_id, rol, texto, modelo, modo, ts, audio, trazado) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (aid, rol, texto, modelo, modo, time.time(), audio or '', trazado or ''))


def db_historial(aid, n=12, as_json=False, modo='agente'):
    """Últimos n mensajes EN ESE MODO, en orden cronológico.

    Devuelve [(rol_api, texto)] para el prompt, o {mensajes:[{rol,texto,modo}]}
    si as_json=True (rol legible: usuario/agente).
    """
    with conectar() as c:
        filas = c.execute(
            "SELECT rol, texto FROM conversaciones WHERE agente_id=? AND modo=? "
            "ORDER BY id DESC LIMIT ?", (aid, modo, n)).fetchall()
    filas = list(reversed(filas))
    if as_json:
        return {'mensajes': [
            {'rol': 'usuario' if r['rol'] == 'humano' else 'agente',
             'texto': r['texto'], 'modo': modo} for r in filas]}
    return [(('user' if r['rol'] == 'humano' else 'assistant'), r['texto'])
            for r in filas]
