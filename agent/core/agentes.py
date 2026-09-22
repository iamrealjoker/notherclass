# -*- coding: utf-8 -*-
"""core/agentes.py — convención de carpetas autocontenidas por agente.

Cada agente vive en una carpeta  Ag.<Nombre>  en la RAÍZ del repo con su propia
persona, memoria, logs y un agente.json (tipo "ag"). El motor (agent/run.py) es
compartido; agentconfig.py apunta a la carpeta vía tokens {carpeta}/{repo}.

Estructura de una carpeta de agente:
    Ag.HorasExtras/
      ├── agente.json      # metadatos: id, _tipo:"ag", nombre, store, folder...
      ├── persona.md       # identidad (rellenada desde _plantilla_agente.md)
      ├── memory/          # brain_<store>.sqlite3
      └── logs/            # agent.log
"""
import json
import os
import re
from datetime import datetime, timezone

try:
    from . import agentconfig as _ac
except Exception:  # noqa: BLE001  (si se importa suelto)
    import agentconfig as _ac


def repo_root():
    return _ac.repo_root()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def folder_from_name(name):
    """'Horas Extras' -> 'Ag.HorasExtras' ; 'Joker' -> 'Ag.Joker'."""
    base = re.sub(r"[^A-Za-z0-9]+", "", name or "").strip()
    return ("Ag." + base) if base else None


def _plantilla():
    return os.path.join(repo_root(), "agent", "AGENTS", "_plantilla_agente.md")


def persona_contenido(name, rol, persona="", values=""):
    """Personalidad del agente.

    Si el agente creador nos pasa una `persona` completa (redactada tras
    entrevistar al humano), se usa ESA tal cual. Si no, se rellena la plantilla
    con {NOMBRE}/{ROL}/{VALORES}.
    """
    if (persona or "").strip():
        return persona.strip() + "\n"
    p = _plantilla()
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        t = fh.read()
    return (t.replace("{NOMBRE}", name)
             .replace("{ROL}", rol or "asistente de IA de larga duración")
             .replace("{VALORES}", (values or "").strip()
                      or "Empático, resolutivo y proactivo con tu humano."))


def crear_carpeta(id, name, rol="", desc="", token="", workspace=None,
                  persona="", values=""):
    """Crea Ag.<Nombre>/ (persona.md + agente.json + memory/ + logs/) y devuelve
    (carpeta, entry_config, ruta_abs, mensaje_error_o_None)."""
    ws = os.path.abspath(workspace) if workspace else repo_root()
    name = (name or id).strip()
    folder = folder_from_name(name)
    if not folder:
        return None, None, None, f"no pude derivar carpeta del nombre {name!r}"
    fdir = os.path.join(ws, folder)
    if os.path.exists(fdir):
        return folder, None, fdir, f"ya existe la carpeta {fdir}"
    os.makedirs(os.path.join(fdir, "memory"), exist_ok=True)
    os.makedirs(os.path.join(fdir, "logs"), exist_ok=True)
    contenido = persona_contenido(name, rol, persona=persona, values=values)
    if contenido is not None:
        with open(os.path.join(fdir, "persona.md"), "w", encoding="utf-8") as fh:
            fh.write(contenido)
    meta = {
        "_tipo": "ag", "id": id, "nombre": name, "store": id, "folder": folder,
        "rol": (rol or "").strip(), "desc": (desc or "").strip(),
        "creado": _now(), "estado": "creado",
        "persona": "{carpeta}/persona.md",
        "memoria": "{carpeta}/memory",
        "logs": "{carpeta}/logs",
    }
    if token:
        meta["telegram_bot_token"] = token
    with open(os.path.join(fdir, "agente.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    entry = {
        "_tipo": "ag",
        "_desc": desc or f"Agente tipo 'ag' en {folder} (rol: {rol or '—'})",
        "name": name, "store": id, "carpeta": folder,
        "persona": "{carpeta}/persona.md",
        "memory_dir": "{carpeta}/memory",
        "log_file": "{carpeta}/logs/agent.log",
        # IMPORTANTE: un agente RECIEN creado NO arranca solo. Si arrancara sin
        # token de Telegram ni proveedor configurado, el supervisor lo lanzaria y
        # el agente intentaria llamar al LLM -> errores 401 en bucle.
        # Cuando el agente este listo, pon "_supervisar": true.
        "_supervisar": False,
    }
    if token:
        entry["telegram_bot_token"] = token
    return folder, entry, fdir, None
