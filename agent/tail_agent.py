"""
tail_agent.py — mira en vivo / después lo que hace el agente y QUÉ aprendió.

Uso:
  python tail_agent.py                 # últimas 30 líneas del log + estado neto
  python tail_agent.py --follow        # se queda siguiendo el log (como tail -f)
  python tail_agent.py --net           # solo el estado 'neto' de la memoria
  python tail_agent.py --count 100     # últimas 100 líneas

Así, tras hablar por Telegram con el agente, vuelves aquí y ves: qué hizo,
cuánto gastó, y si consolidó (bruto→neto) o solo acumuló datos.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from brain import Brain
from core.logger import ActivityLog


def show_net():
    store = os.environ.get("TW_AGENT_STORE") or os.environ.get("TW_AGENT_NAME", "Forja")
    mem_dir = os.environ.get("TW_MEMORY_DIR", os.path.join(HERE, "MEMORY"))
    if not os.path.isabs(mem_dir):
        mem_dir = os.path.join(HERE, mem_dir)
    if not os.path.exists(mem_dir):
        print("(todavía no hay memoria: MEMORY/)")
        return
    brain = Brain(mem_dir, agent_id=store)
    beliefs = brain.beliefs(limit=100)
    lessons = brain.lessons(limit=100)
    unproc = brain.unprocessed_chunks(limit=1)
    nsum = brain._conn.execute(
        "SELECT count(*) AS n FROM chunks WHERE agent=? AND kind='summary'",
        (store,)).fetchone()
    nraw = brain._conn.execute(
        "SELECT count(*) AS n FROM chunks WHERE agent=? AND kind IN ('user','assistant','event')",
        (store,)).fetchone()
    print("╭─ 🧠 MEMORIA NETO ─────────────────────────────────")
    print(f"│ creencias      : {len(beliefs)}")
    print(f"│ victorias ✅   : {sum(1 for l in lessons if l['kind']=='win')}")
    print(f"│ fallos ❌      : {sum(1 for l in lessons if l['kind']=='fail')}")
    print(f"│ resúmenes 📚   : {nsum['n'] if nsum else 0}  (neto consolidado)")
    print(f"│ bruto total    : {nraw['n'] if nraw else 0}")
    print(f"│ bruto pendiente: {len(unproc)}  (aún sin consolidar)")
    print("├─ creencias ───────────────────────────────────────")
    for b in beliefs:
        print(f"│ · {b['key']} = {b['value']} (conf {b['confidence']:.2f})")
    if not beliefs:
        print("│ (ninguna)")
    print("├─ lecciones ───────────────────────────────────────")
    for l in lessons[:12]:
        tag = "✅" if l["kind"] == "win" else "❌"
        print(f"│ {tag} {l['title']}: {l['body'][:80]}")
    if not lessons:
        print("│ (ninguna)")
    print("╰───────────────────────────────────────────────────")
    brain.close()


def main():
    ap = argparse.ArgumentParser(description="tail del agente")
    ap.add_argument("--follow", action="store_true", help="seguir el log en vivo")
    ap.add_argument("--net", action="store_true", help="solo estado neto")
    ap.add_argument("--count", type=int, default=30, help="últimas N líneas")
    args = ap.parse_args()

    if args.net:
        show_net()
        return
    show_net()
    print("\n── log de actividad ───────────────────────────────\n")
    log = ActivityLog()
    log.tail(n=args.count, follow=args.follow)


if __name__ == "__main__":
    main()
