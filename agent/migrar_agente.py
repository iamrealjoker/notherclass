#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrar_agente.py — Mueve un agente existente (layout viejo: persona en
agent/AGENTS/, memoria en agent/MEMORY/) a su carpeta autocontenida
Ag.<Nombre>/ en la RAÍZ (tipo 'ag').

Debe ejecutarse con el agente DETENIDO (para no perder escrituras de memoria).
Copia la persona y su brain_<store>.sqlite3 a la carpeta y actualiza la entrada
en configAgentes.json (persona/memoria/logs apuntan a {carpeta}).

Uso:  python3 migrar_agente.py --id horas_extras
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # .../agent
ROOT = os.path.dirname(HERE)                                # .../notherclass
CONFIG = os.path.join(ROOT, "configAgentes.json")


def _read():
    with open(CONFIG, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write(data):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, CONFIG)


def folder_from_name(name):
    base = re.sub(r"[^A-Za-z0-9]+", "", name or "").strip()
    return ("Ag." + base) if base else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--keep-old-persona", action="store_true",
                    help="no borrar la persona antigua en agent/AGENTS/")
    args = ap.parse_args()
    aid = args.id.strip()

    data = _read()
    agents = data.get("agentes", {})
    if aid not in agents:
        print(f"ERROR: no existe '{aid}' en configAgentes.json.")
        sys.exit(1)
    entry = agents[aid]
    if entry.get("carpeta"):
        print(f"Ya migrado: '{aid}' usa carpeta {entry['carpeta']}/.")
        return

    name = entry.get("name", aid)
    store = entry.get("store", aid)
    folder = folder_from_name(name)
    if not folder:
        print(f"ERROR: no pude derivar carpeta de {name!r}.")
        sys.exit(1)
    fdir = os.path.join(ROOT, folder)
    if os.path.exists(fdir) and os.listdir(fdir):
        print(f"ERROR: ya existe y no está vacía {fdir}.")
        sys.exit(1)
    os.makedirs(os.path.join(fdir, "memory"), exist_ok=True)
    os.makedirs(os.path.join(fdir, "logs"), exist_ok=True)
    print(f"Migrando '{aid}' ({name}) → {folder}/")

    # persona
    old_p = entry.get("persona") or ""
    if old_p and not old_p.startswith("{") and os.path.exists(
            os.path.join(HERE, old_p) if not os.path.isabs(old_p) else old_p):
        src = os.path.join(HERE, old_p) if not os.path.isabs(old_p) else old_p
        shutil.copy2(src, os.path.join(fdir, "persona.md"))
        print(f"  ✔ persona copiada: {src} → persona.md")
        if not args.keep_old_persona:
            try:
                os.remove(src)
            except Exception:
                pass
    else:
        print("  ⚠ sin persona previa que copiar (la crearé de plantilla si hace falta).")

    # memoria (brain_<store>.sqlite3*)
    md_old = entry.get("memory_dir") or "MEMORY"
    m_dir = os.path.join(HERE, md_old) if not os.path.isabs(md_old) else md_old
    hits = glob.glob(os.path.join(m_dir, f"brain_{store}*")) + \
           glob.glob(os.path.join(m_dir, f"brain_{store}.sqlite3*"))
    n = 0
    for h in sorted(set(hits)):
        if os.path.isfile(h):
            shutil.copy2(h, os.path.join(fdir, "memory", os.path.basename(h)))
            n += 1
    print(f"  ✔ memoria: {n} fichero(s) brain_{store}* copiados a memory/")

    # agente.json
    meta = {
        "_tipo": "ag", "id": aid, "nombre": name, "store": store,
        "folder": folder, "desc": entry.get("_desc", ""),
        "estado": "migrado",
        "persona": "{carpeta}/persona.md",
        "memoria": "{carpeta}/memory",
        "logs": "{carpeta}/logs",
    }
    if entry.get("telegram_bot_token"):
        meta["telegram_bot_token"] = entry["telegram_bot_token"]
    with open(os.path.join(fdir, "agente.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(f"  ✔ agente.json escrito en {folder}/")

    # actualizar entrada de config
    entry["_tipo"] = "ag"
    entry["carpeta"] = folder
    entry["persona"] = "{carpeta}/persona.md"
    entry["memory_dir"] = "{carpeta}/memory"
    entry["log_file"] = "{carpeta}/logs/agent.log"
    # (se conservan name, store, telegram_bot_token, _desc)
    _write(data)
    print(f"  ✔ configAgentes.json actualizado (carpeta={folder}).")

    print("\nListo. Relanza con:  cd agent && python3 run.py --agent " + aid)
    print("La memoria/memoria nueva se guardará en " + folder + "/memory/")


if __name__ == "__main__":
    main()
