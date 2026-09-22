#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reiniciar_agentes.py — comando para (re)iniciar los agentes SIN tocar a Joker.

Wrapper sencillo sobre agent/core/supervisor.py. Joker es el PID 1 del
contenedor y NO se gestiona aquí (ni se mata ni se arranca).

Ejemplos:
  python3 reiniciar_agentes.py                 # reinicia TODOS menos Joker
  python3 reiniciar_agentes.py --estado        # ¿quién está vivo?
  python3 reiniciar_agentes.py --solo horas_extras
  python3 reiniciar_agentes.py --detener       # para todos menos Joker
  python3 reiniciar_agentes.py --watcher       # arranca el watcher de auto-reinicio
  python3 reiniciar_agentes.py --list
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "agent"))

from core import supervisor as sup  # noqa: E402


def _traducir_args(argv):
    """Traduce el CLI amable a las opciones de supervisor.main()."""
    out = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--solo", "--agente"):
            i += 1
            if i < len(argv):
                out.append(argv[i])
        elif a == "--reiniciar":
            pass  # es la acción por defecto
        elif a == "--watcher":
            out.append("--asegurar-watch")
        elif a in ("--estado", "--list", "--detener", "--arrancar", "--watch",
                   "--ayuda", "-h", "--help"):
            out.append(a)
        else:
            out.append(a)
        i += 1
    return out


def main():
    argv = sys.argv[1:]
    # --watcher: arranca el watcher DETACHED (no bloquea la terminal)
    if "--watcher" in argv or "--asegurar-watch" in argv:
        pid = sup.ensure_watcher()
        print(f"watcher asegurado: pid {pid}")
        print(sup.estado())
        return
    if not argv:
        # por defecto: reiniciar todos menos Joker
        sys.argv = [sys.argv[0], "--reiniciar"]
        sup.main()
        return
    sys.argv = [sys.argv[0]] + _traducir_args(argv)
    sup.main()


if __name__ == "__main__":
    main()
