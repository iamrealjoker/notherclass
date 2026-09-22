# -*- coding: utf-8 -*-
"""pizarra.py — Blueprint de la PIZARRA compartida (consola, JokerV2).

SOLO-LECTURA. Expone la pizarra bot<->bot (grupo_bus/chat_*.jsonl) al panel.

Contrato con app.py (no romper):
  - nombre del blueprint : bp_pizarra
  - ruta                 : GET /api/pizarra
"""
import glob
import json
import os

from flask import Blueprint, jsonify, request

# config.py vive en consola/ y su RAIZ es la raíz del proyecto (/app/notherclass).
from config import RAIZ

bp_pizarra = Blueprint('pizarra', __name__)

BUS_DIR = os.path.join(RAIZ, 'grupo_bus')


def _rutas_pizarra():
    """Todas las pizarras disponibles, ordenadas por chat_id."""
    return sorted(glob.glob(os.path.join(BUS_DIR, 'chat_*.jsonl')))


def _chat_de_ruta(ruta):
    base = os.path.basename(ruta)                 # chat_-1003994605086.jsonl
    return base[len('chat_'):-len('.jsonl')]


def _leer(ruta, limite=None):
    """Lee un .jsonl tolerante: ignora líneas corruptas o vacías.

    Devuelve lista de dicts, cada uno con un índice opcional 'i'.
    No lanza si una línea no es JSON válido: la salta (la pizarra la
    escriben varios procesos y nunca debe tumbar el panel).
    """
    lineas = []
    try:
        with open(ruta, encoding='utf-8') as f:
            for i, ln in enumerate(f):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                if isinstance(d, dict):
                    d.setdefault('i', i)
                    lineas.append(d)
    except FileNotFoundError:
        return []
    if limite:
        lineas = lineas[-limite:]
    return lineas


@bp_pizarra.route('/api/pizarra')
def api_pizarra():
    """Lista de pizarras + (opcional) el contenido de una.

    Query:
      ?chat=<id>   devuelve SOLO las líneas de esa pizarra
      ?limit=<n>   últimas n líneas (default 40)
    Sin ?chat devuelve el catálogo de pizarras y sus tamaños.
    """
    try:
        limite = int(request.args.get('limit', 40))
    except (TypeError, ValueError):
        limite = 40
    limite = max(1, min(limite, 500))

    chat = request.args.get('chat')
    pizarras = _rutas_pizarra()

    if chat:
        ruta = os.path.join(BUS_DIR, f'chat_{chat}.jsonl')
        if not os.path.exists(ruta):
            return jsonify({'error': f'pizarra {chat} no existe'}), 404
        lineas = _leer(ruta, limite)
        return jsonify({
            'chat': chat,
            'total_lineas': len(_leer(ruta)),
            'limit': limite,
            'mensajes': lineas,
        })

    catalogo = []
    for ruta in pizarras:
        todas = _leer(ruta)
        catalogo.append({
            'chat': _chat_de_ruta(ruta),
            'lineas': len(todas),
            'ultimo_ts': todas[-1].get('ts') if todas else None,
            'archivo': os.path.basename(ruta),
        })
    return jsonify({'pizarras': catalogo, 'limit': limite})
