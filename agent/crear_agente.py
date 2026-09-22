#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crear_agente.py — Plantilla para crear un agente nuevo "tipo Joker" de forma
rápida y consistente (escalable a miles).

Qué hace:
  1) Copia la persona base (agent/AGENTS/_plantilla_agente.md) a agent/AGENTS/<id>.md
     rellenando {NOMBRE} / {ROL} / {VALORES}.
  2) Registra el agente en configAgentes.json (sección "agentes") con lo mínimo:
     name, store, persona y telegram_bot_token. El resto (DeepSeek, memoria, voz,
     BigBoss deepseek-v4-pro, lilJoker, budgets…) lo hereda de "_defaults" → igual
     que Joker.
  3) Te dice cómo lanzarlo.

Uso:
  cd /app/notherclass/agent
  python3 crear_agente.py --id misi --name "Misi" --rol "asistente de ventas" --token 123:TOKEN
  # luego:  python3 run.py --agent misi

Opciones:
  --dry-run        solo muestra qué haría (no escribe nada).
  --no-persona     no genera el archivo de persona (usa una ruta ya existente vía --persona).
  --persona RUTA    ruta (relativa a agent/) de la persona a usar en vez de generar una.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # .../agent
ROOT = os.path.dirname(HERE)                                # .../notherclass (repo)
CONFIG = os.path.join(ROOT, "configAgentes.json")
AGENTS_DIR = os.path.join(HERE, "AGENTS")
PLANTILLA = os.path.join(AGENTS_DIR, "_plantilla_agente.md")

VALID_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _read_config():
    if not os.path.exists(CONFIG):
        print(f"ERROR: no existe {CONFIG}. Revisa que estés en el repo de NotherClass.")
        sys.exit(1)
    with open(CONFIG, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_config(data):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, CONFIG)


def _rellenar_plantilla(nombre, rol, valores):
    if not os.path.exists(PLANTILLA):
        return None
    with open(PLANTILLA, "r", encoding="utf-8") as fh:
        t = fh.read()
    return (t.replace("{NOMBRE}", nombre)
             .replace("{ROL}", rol or "asistente de IA de larga duración")
             .replace("{VALORES}", valores or "Empático, resolutivo y proactivo con tu humano."))


def main():
    ap = argparse.ArgumentParser(description="Crea un agente nuevo tipo Joker")
    ap.add_argument("--id", required=True, help="slug único (p. ej. misi)")
    ap.add_argument("--name", help="nombre visible (default = --id)")
    ap.add_argument("--rol", help="propósito/rol del agente (va a su persona)")
    ap.add_argument("--token", help="token de Telegram del bot nuevo (@BotFather)")
    ap.add_argument("--desc", help="nota corta _desc")
    ap.add_argument("--persona", help="ruta de persona a usar (relativa a agent/) en vez de generar")
    ap.add_argument("--no-persona", action="store_true", help="no generar archivo de persona")
    ap.add_argument("--dry-run", action="store_true", help="solo mostrar qué haría")
    args = ap.parse_args()

    aid = args.id.strip()
    if not VALID_ID.match(aid):
        print("ERROR: --id debe ser alfanumérico (letras/números/_/-). Ej: misi, agente_2.")
        sys.exit(1)
    nombre = (args.name or aid).strip()
    rol = (args.rol or "").strip()
    desc = (args.desc or f"Agente creado desde la plantilla (rol: {rol or '—'})").strip()

    data = _read_config()
    agentes = data.setdefault("agentes", {})
    if aid in agentes:
        print(f"ERROR: ya existe un agente con id '{aid}' en configAgentes.json.")
        sys.exit(1)

    # ── crear carpeta autocontenida Ag.<Nombre>/ (tipo 'ag') vía core.agentes ──
    from core import agentes as _am
    if args.dry_run:
        import tempfile
        _ws = tempfile.mkdtemp(prefix="ag_dryrun_")
    else:
        _ws = ROOT
    carpeta, entry, fdir, err = _am.crear_carpeta(
        aid, nombre, rol=rol, desc=desc, token=args.token or "", workspace=_ws)
    if err:
        print("⚠️", err)
        sys.exit(1)
    if args.persona:
        # persona personalizada (opcional): copiar a la carpeta del agente
        src = args.persona if os.path.isabs(args.persona) else os.path.join(HERE, args.persona)
        if not os.path.exists(src):
            print(f"ERROR: no existe la persona {src}.")
            sys.exit(1)
        import shutil
        shutil.copy(src, os.path.join(fdir, "persona.md"))

    print("=" * 78)
    print(f"Agente nuevo: {nombre} (id={aid}, tipo 'ag')")
    print(f"  carpeta autocontenida: {carpeta}/")
    print(f"  persona: {carpeta}/persona.md")
    print(f"  memoria: {carpeta}/memory/brain_{aid}.sqlite3")
    print("  hereda de _defaults: DeepSeek deepseek-v4-flash · memoria infinita · "
          "voz local gratis · BigBoss deepseek-v4-pro · lilJoker")
    print(f"  telegram token: {'SÍ' if args.token else 'FALTA (sácalo de @BotFather)'}")
    print("=" * 78)

    if args.dry_run:
        print("\n[DRY-RUN] No registré nada en config. Entrada que se añadiría a 'agentes':")
        print(json.dumps({aid: entry}, ensure_ascii=False, indent=2))
        print("Estructura que crearía en " + carpeta + "/:",
              sorted(os.listdir(fdir)))
        import shutil
        shutil.rmtree(_ws, ignore_errors=True)
        return

    data = _read_config()
    agentes = data.setdefault("agentes", {})
    if aid in agentes:
        import shutil
        shutil.rmtree(fdir, ignore_errors=True)
        print(f"ERROR: ya existe un agente con id '{aid}' en configAgentes.json.")
        sys.exit(1)
    agentes[aid] = entry
    _write_config(data)
    print(f"✔ Carpeta creada: {carpeta}/  → registrado en {CONFIG}")

    print("\nSiguientes pasos:")
    if not args.token:
        print("  1. Crea el bot en Telegram con @BotFather (/newbot) y copia su token.")
        print(f"     Edita configAgentes.json -> agentes.{aid}.telegram_bot_token "
              "(o vuelve a pasar --token).")
    print("  2. Lánzalo:   cd /app/notherclass/agent && python3 run.py --agent " + aid)
    print("     (texto y voz; el audio es local, 0 tokens).")
    print("  3. Ver agentes: python3 run.py --list-agents")


if __name__ == "__main__":
    main()

