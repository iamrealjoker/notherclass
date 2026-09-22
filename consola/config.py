# -*- coding: utf-8 -*-
"""config.py — acceso a configAgentes.json y cerebros (SOLO-LECTURA).

La consola nunca escribe en los datos de los agentes: solo lee su config,
su persona, su cerebro SQLite y la cola de su log. Lógica oculta al front.
"""
import json
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(RAIZ, 'configAgentes.json')


def leer_config_agentes():
    with open(CONFIG, encoding='utf-8') as f:
        return json.load(f)


def _resolver_vars(val, cfg):
    """Resuelve $keys.nombre y {carpeta} en los valores de config."""
    if not isinstance(val, str):
        return val
    if val.startswith('$keys.'):
        return cfg.get('api_keys', {}).get(val[6:], '')
    return val.replace('{carpeta}', '')


def resolver_agente(cfg, aid):
    """Devuelve la config efectiva del agente (defaults < agente)."""
    d = dict(cfg.get('_defaults', {}))
    a = cfg['agentes'][aid]
    d.update({k: v for k, v in a.items() if not k.startswith('_llm_')})
    for k in ('_desc', 'name', 'store', 'carpeta'):
        if k in a:
            d[k] = a[k]
    return d


def resolver_unidad(cfg, aid):
    """Como resolver_agente pero acepta también servicios_extra (p.ej. traductor).

    Devuelve (config_efectiva, tipo) con tipo='agente'|'servicio', o
    KeyError si el id no existe en ninguna de las dos secciones.
    """
    if aid in cfg.get('agentes', {}):
        return resolver_agente(cfg, aid), 'agente'
    se = cfg.get('servicios_extra', {})
    if isinstance(se, dict) and aid in se and not aid.startswith('_'):
        d = dict(cfg.get('_defaults', {}))
        d.update({k: v for k, v in se[aid].items() if not k.startswith('_')})
        return d, 'servicio'
    raise KeyError(aid)


def carpeta_de(a):
    return os.path.join(RAIZ, a.get('carpeta', ''))


def _abrir_cerebro(a):
    mem = a.get('memory_dir', '').replace('{carpeta}', carpeta_de(a)) \
        if '{carpeta}' in a.get('memory_dir', '') else \
        os.path.join(carpeta_de(a), 'memory')
    store = a.get('store', '')
    ruta = os.path.join(mem, f'brain_{store}.sqlite3')
    if not os.path.exists(ruta):
        return None
    c = sqlite3_con(ruta)
    return c


def sqlite3_con(ruta):
    import sqlite3
    c = sqlite3.connect(f'file:{ruta}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def cerebro_de(a):
    """Conexión solo-lectura al cerebro del agente, o None si no existe."""
    return _abrir_cerebro(a)


def contadores_de(cer):
    """(chunks, creencias, lecciones) de un cerebro."""
    if not cer:
        return (0, 0, 0)
    return (cer.execute('SELECT COUNT(*) FROM chunks').fetchone()[0],
            cer.execute('SELECT COUNT(*) FROM beliefs').fetchone()[0],
            cer.execute('SELECT COUNT(*) FROM lessons').fetchone()[0])


def llm_de_agente(a):
    """Datos de la llamada LLM del agente (para chat real)."""
    return {
        'proveedor': a.get('llm_provider', ''),
        'modelo': a.get('llm_model', ''),
        'base_url': a.get('llm_base_url', ''),
        'api_key': _resolver_vars(a.get('llm_api_key', ''), leer_config_agentes()),
        'reasoning_effort': a.get('reasoning_effort', 'low'),
    }


def resumen_cerebro(a, max_beliefs=10, max_lessons=5):
    """Contexto curado del agente para inyectar en el system prompt del chat."""
    cer = cerebro_de(a)
    if not cer:
        return '(sin memoria aún)'
    bels = cer.execute(
        'SELECT key, value, confidence FROM beliefs '
        'ORDER BY confidence DESC LIMIT ?', (max_beliefs,)).fetchall()
    less = cer.execute(
        'SELECT kind, title FROM lessons ORDER BY rowid DESC LIMIT ?',
        (max_lessons,)).fetchall()
    lineas = ['CREENCIAS:']
    lineas += [f'- {b["key"]}: {b["value"]} (conf {b["confidence"]:.2f})' for b in bels]
    if less:
        lineas.append('LECCIONES:')
        lineas += [f'- [{l["kind"]}] {l["title"]}' for l in less]
    cer.close()
    return '\n'.join(lineas)


def tail_log(a, n=60):
    """Últimas n líneas del log del agente (solo texto, sin rutas)."""
    ruta = os.path.join(carpeta_de(a), 'logs', 'agent.log')
    if not os.path.exists(ruta):
        return []
    with open(ruta, 'rb') as f:
        f.seek(0, 2)
        tam = f.tell()
        f.seek(max(0, tam - 200000))
        lineas = f.read().decode('utf-8', 'replace').splitlines()
    return lineas[-n:]
