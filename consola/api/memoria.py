# -*- coding: utf-8 -*-
"""memoria.py — lectura SOLO-LECTURA del cerebro SQLite de un agente.

GET /api/memoria/<aid>?tipo=chunks|beliefs|lessons&page=1
  -> {items:[...], total, page}

GET /api/memoria/<aid>?tipo=persona
  -> {items:[{texto}], total, page:1, fuente:'persona.md'}  (submenú 👤 Persona)
"""
import os
import json
import sqlite3

from flask import Blueprint, jsonify, request

from config import (leer_config_agentes, resolver_agente, cerebro_de,
                    carpeta_de)

bp = Blueprint('memoria', __name__)
POR_PAG = 25

CONSULTAS = {
    'chunks': 'SELECT rowid AS id, * FROM chunks ORDER BY rowid DESC',
    'beliefs': ('SELECT key, value, confidence FROM beliefs '
                'ORDER BY confidence DESC'),
    'lessons': 'SELECT rowid AS id, kind, title, outcome FROM lessons ORDER BY rowid DESC',
}


def _persona(a):
    """Lee la persona del agente (Ag.X/persona.md). Devuelve (texto, fuente)."""
    for nombre in ('persona.md', 'persona.txt'):
        ruta = os.path.join(carpeta_de(a), nombre)
        if os.path.exists(ruta):
            with open(ruta, encoding='utf-8', errors='replace') as f:
                return f.read(), nombre
    return '', None


@bp.route('/api/memoria/<aid>')
def memoria(aid):
    cfg = leer_config_agentes()
    if aid not in cfg['agentes']:
        return jsonify({'error': 'agente desconocido'}), 404
    a = resolver_agente(cfg, aid)
    tipo = request.args.get('tipo', 'chunks')
    if tipo == 'persona':
        texto, fuente = _persona(a)
        if not fuente:
            return jsonify({'items': [], 'total': 0, 'page': 1,
                            'fuente': None})
        return jsonify({'items': [{'texto': texto, 'fuente': fuente}],
                        'total': 1, 'page': 1, 'fuente': fuente})
    if tipo not in CONSULTAS:
        return jsonify({'error': 'tipo debe ser chunks|beliefs|lessons|persona'}), 400
    cer = cerebro_de(a)
    if not cer:
        return jsonify({'items': [], 'total': 0, 'page': 1})
    try:
        filas = cer.execute(CONSULTAS[tipo]).fetchall()
    except sqlite3.Error as e:
        cer.close()
        return jsonify({'error': f'esquema: {e}'}), 500
    total = len(filas)
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    ini = (page - 1) * POR_PAG
    items = [dict(r) for r in filas[ini:ini + POR_PAG]]
    cer.close()
    humano = request.args.get('humano') in ('1', 'true', 'si', 'sí', 'yes')
    if humano:
        items = _humanizar(tipo, items)
    resp = {'items': items, 'total': total, 'page': page}
    if humano:
        resp['modo'] = 'humano'
        resp['editable'] = EDITABLES[tipo]['campos']
    return jsonify(resp)


# ---------------------------------------------------------------------------
# Modo humano: datos legibles + edición de lo editable (JokerV2)
# ---------------------------------------------------------------------------
# Campos que el front puede editar por tipo. Los NO listados nunca se tocan.
EDITABLES = {
    'chunks':  {'campos': ['text']},
    'beliefs': {'campos': ['value', 'confidence']},
    'lessons': {'campos': ['title', 'outcome']},
}

# Campos que se "resumen" en modo humano (ruido para un humano).
_RESUMEN = {'chunks': ['embedding']}


def _cerebro_path(a):
    """Ruta al SQLite del cerebro del agente (misma lógica que config)."""
    md = a.get('memory_dir', '')
    if '{carpeta}' in md:
        mem = md.replace('{carpeta}', carpeta_de(a))
    else:
        mem = os.path.join(carpeta_de(a), 'memory')
    return os.path.join(mem, f"brain_{a.get('store', '')}.sqlite3")


def _humanizar(tipo, items):
    """Hace los items legibles: resumen embeddings y parsea meta (JSON)."""
    resumen = _RESUMEN.get(tipo, [])
    out = []
    for it in items:
        d = dict(it)
        for k in resumen:
            if k not in d:
                continue
            v = d[k]
            try:
                n = len(json.loads(v)) if isinstance(v, str) else len(v or [])
                d[k] = f'(vector de {n} dims, oculto en modo humano)'
            except Exception:
                d[k] = '(vector)'
        if isinstance(d.get('meta'), str):
            try:
                d['meta'] = json.loads(d['meta'] or '{}')
            except Exception:
                pass
        if 'ts' in d:
            try:
                import time as _t
                d['fecha'] = _t.strftime('%Y-%m-%d %H:%M:%S',
                                         _t.localtime(float(d['ts'])))
            except Exception:
                pass
        out.append(d)
    return out


@bp.route('/api/memoria/<aid>/editar', methods=['POST'])
def memoria_editar(aid):
    """Edita SOLO los campos permitidos de un item del cerebro del agente.

    POST {tipo:'chunks', clave:<rowid>, campos:{text:'...'}}
    Devuelve {ok, filas} o {error}. No borra nunca filas.
    """
    cfg = leer_config_agentes()
    if aid not in cfg.get('agentes', {}):
        return jsonify({'error': 'agente desconocido'}), 404
    a = resolver_agente(cfg, aid)
    d = request.get_json(silent=True) or {}
    tipo = d.get('tipo', '')
    if tipo not in EDITABLES:
        return jsonify({'error': f'tipo no editable: {tipo}'}), 400
    clave = d.get('clave')
    if clave is None or clave == '':
        return jsonify({'error': 'falta clave del item'}), 400
    campos = d.get('campos') or {}
    permitidos = [c for c in EDITABLES[tipo]['campos'] if c in campos]
    if not permitidos:
        return jsonify({'error': 'nada que editar',
                        'campos_permitidos': EDITABLES[tipo]['campos']}), 400
    ruta = _cerebro_path(a)
    if not ruta or not os.path.exists(ruta):
        return jsonify({'error': 'el agente no tiene cerebro'}), 404
    sets = ', '.join(f'{k}=?' for k in permitidos)
    vals = [campos[k] for k in permitidos]
    try:
        con = sqlite3.connect(ruta)          # lectura-escritura solo aquí
        try:
            if tipo == 'beliefs':
                cur = con.execute(f'UPDATE beliefs SET {sets} WHERE key=?',
                                  (*vals, clave))
            else:  # chunks y lessons comparten clave = rowid
                cur = con.execute(f'UPDATE {tipo} SET {sets} WHERE rowid=?',
                                  (*vals, clave))
            filas = cur.rowcount
            con.commit()
        finally:
            con.close()
    except sqlite3.Error as e:
        return jsonify({'error': f'sql: {e}'}), 500
    return jsonify({'ok': filas > 0, 'tipo': tipo, 'clave': clave,
                    'filas': filas, 'campos': permitidos})
