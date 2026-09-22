#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sembrar_agente.py — siembra el cerebro de un agente NUEVO con lo aprendido por
otro (por defecto Joker/Forja): creencias + resúmenes (neto). Así el agente nuevo
"nace" sabiendo el proyecto, SIN heredar toda la conversación ni los chunks sueltos.

Uso:
  cd /app/notherclass/agent
  python3 sembrar_agente.py --destino jokerv1
  python3 sembrar_agente.py --destino jokerv2 --origen Forja
  python3 sembrar_agente.py --destino x --no-resumenes     # solo creencias
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))     # .../agent
ROOT = os.path.dirname(HERE)                          # .../notherclass
sys.path.insert(0, HERE)
from brain import Brain  # noqa: E402


def mem_dir(agent_id):
    cfgp = os.path.join(ROOT, "configAgentes.json")
    data = json.load(open(cfgp, encoding="utf-8")) if os.path.exists(cfgp) else {}
    ent = (data.get("agentes") or {}).get(agent_id) or {}
    md = ent.get("memory_dir") or (data.get("_defaults") or {}).get("memory_dir", "MEMORY")
    carpeta = ent.get("carpeta")
    md = str(md).replace("{repo}", ROOT)
    if carpeta:
        md = md.replace("{carpeta}", os.path.join(ROOT, carpeta))
    return md if os.path.isabs(md) else os.path.join(HERE, md)


def main():
    ap = argparse.ArgumentParser(description="Siembra un agente con la memoria de otro")
    ap.add_argument("--destino", required=True, help="id del agente nuevo")
    ap.add_argument("--origen", default="Forja", help="store del que copiar (default Forja)")
    ap.add_argument("--no-resumenes", action="store_true", help="no copiar resúmenes")
    ap.add_argument("--no-creencias", action="store_true", help="no copiar creencias")
    args = ap.parse_args()

    src_mem, dst_mem = mem_dir(args.origen), mem_dir(args.destino)
    print(f"origen : {args.origen}  ->  {src_mem}")
    print(f"destino: {args.destino}  ->  {dst_mem}")
    if src_mem == dst_mem:
        print("⚠ mismo cerebro: nada que hacer.")
        return

    src = Brain(src_mem, agent_id=args.origen)
    dst = Brain(dst_mem, agent_id=args.destino)

    beliefs = [] if args.no_creencias else src.beliefs(limit=200)
    sums = []
    if not args.no_resumenes:
        sums = [dict(r) for r in src._conn.execute(
            "SELECT text FROM chunks WHERE agent=? AND kind='summary' ORDER BY ts DESC LIMIT 20",
            (args.origen,)).fetchall()]

    # nombre visible del destino (para corregir la identidad al sembrar)
    cfgp = os.path.join(ROOT, "configAgentes.json")
    data = json.load(open(cfgp, encoding="utf-8")) if os.path.exists(cfgp) else {}
    nombre = ((data.get("agentes") or {}).get(args.destino) or {}).get("name", args.destino)

    n = 0
    for b in beliefs:
        k, v = b["key"], b["value"]
        if k in ("identidad.nombre", "identity.name", "nombre"):
            v = f"El agente se llama {nombre}"     # su identidad, no la de Joker
        dst.set_belief(k, v, confidence=b.get("confidence", 0.8), source="sembrado")
        n += 1

    m = 0
    if sums:
        existentes = {r["text"] for r in dst._conn.execute(
            "SELECT text FROM chunks WHERE agent=? AND kind='summary'", (args.destino,))}
        for s in sums:
            if s["text"] in existentes:
                continue
            dst.add_summary_chunk(s["text"], meta={"sembrado_de": args.origen})
            m += 1

    print(f"✔ {args.destino}: sembradas {n} creencias y {m} resúmenes desde {args.origen}")
    src.close()
    dst.close()


if __name__ == "__main__":
    main()
