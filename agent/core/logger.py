"""
core/logger.py — log de actividad del agente (para poder hacer "tail").

Cada evento relevante (turno, skill, consolidación, gasto de tokens) se escribe
como una línea append en logs/agent.log con marca de tiempo, para revisarlo
después: qué hizo, cuánto gastó, y QUÉ aprendió de verdad (neto vs bruto).
"""
import json
import os
import sys
from datetime import datetime, timezone

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class ActivityLog:
    def __init__(self, path: str = None):
        if path is None:
            path = os.environ.get("TW_LOG_FILE", os.path.join(_root, "logs", "agent.log"))
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def write(self, kind: str, data: dict):
        try:
            line = json.dumps({"ts": _ts(), "kind": kind, **data},
                              ensure_ascii=False)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            sys.stderr.write(f"[log] no pude escribir: {e}\n")

    def tail(self, n: int = 30, follow: bool = False):
        """Printea las últimas n líneas; con follow=True se queda siguiendo el archivo."""
        if not os.path.exists(self.path):
            print(f"(log vacío aún: {self.path})")
            if not follow:
                return
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-n:]:
            print(_pretty(line))

        if not follow:
            return
        import time
        with open(self.path, "r", encoding="utf-8") as f:
            f.seek(0, 2)          # al final
            pos = f.tell()
            try:
                while True:
                    line = f.readline()
                    if line:
                        print(_pretty(line))
                    else:
                        time.sleep(0.5)
            except KeyboardInterrupt:
                print("\n(observando log, fin)")



def _pretty(line: str) -> str:
    try:
        d = json.loads(line)
        k = d.get("kind", "?")
        if k == "turn":
            return (f"[{d.get('ts')}] TURNO user={d.get('user','')[:60]!r} "
                    f"tools={d.get('tools')} in={d.get('in')} out={d.get('out')} "
                    f"cost=${d.get('cost',0):.5f}")
        if k == "consolidate":
            return (f"[{d.get('ts')}] 🛌 CONSOLIDÓ: bruto={d.get('raw_read')} "
                    f"neto: summary={bool(d.get('summary_saved'))} "
                    f"beliefs={d.get('beliefs_applied')} lessons={d.get('lessons_applied')}")
        if k == "reply":
            return (f"[{d.get('ts')}] ▶ {d.get('agent')}: "
                    f"{(d.get('text') or '')[:200]}")
        if k == "learn":
            return f"[{d.get('ts')}] 🧠 aprendió {d.get('kind')}: {d.get('title')}"
        if k == "tool":
            return (f"[{d.get('ts')}] 🛠 {d.get('name')} {d.get('args')} → "
                    f"{(d.get('preview') or '')[:120]}")
        if k == "error":
            return f"[{d.get('ts')}] ❌ ERROR: {d.get('message')}\n{d.get('trace','')}"
        return f"[{d.get('ts')}] {k}: {json.dumps({x: d[x] for x in d if x != 'ts'}, ensure_ascii=False)}"
    except Exception:
        return line.rstrip()
