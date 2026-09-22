# -*- coding: utf-8 -*-
"""Paquete API de la consola (JokerV2).

Módulos SOLO-LECTURA que la consola consume como blueprints Flask:

  - memoria.py  -> /api/memoria/<aid>        (cerebros SQLite de Ag.X/memory/)
  - pizarra.py  -> /api/pizarra              (grupo_bus/*.jsonl)
  - agentes.py  -> /api/panel/agentes        (estado/config de agentes, sin chocar
                                              con /api/agentes de app.py)

Ninguno de estos módulos escribe en los datos de los agentes.
"""
