# -*- coding: utf-8 -*-
"""agentes.py — Blueprint de estado/config de agentes (consola, JokerV2).

SOLO-LECTURA. Complementa a /api/agentes de app.py (que ya lista agentes):
aquí el panel obtiene el pulso de PROCESOS y pizarras sin duplicar lógica
de personas/cerebros.

Contrato con app.py (no romper):
  - nombre del blueprint : bp_agentes
  - ruta                 : GET /api/panel/agentes
"""
import os
import json
import subprocess

import requests
from flask import Blueprint, jsonify, request

from config import (RAIZ, leer_config_agentes, resolver_agente, carpeta_de,
                    contadores_de, cerebro_de, llm_de_agente)

bp_agentes = Blueprint('agentes', __name__)


def _procesos_vivos():
    """PIDs de los daemons de agentes (run.py --agent X), tolerante a fallos."""
    try:
        out = subprocess.run(
            ['ps', '-eo', 'pid,args'],
            capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return {}
    vivos = {}
    for ln in out.splitlines():
        if 'run.py' not in ln or '--agent' not in ln:
            continue
        try:
            pid = int(ln.split(None, 1)[0])
        except (ValueError, IndexError):
            continue
        partes = ln.split()
        if '--agent' in partes:
            aid = partes[partes.index('--agent') + 1]
            vivos[aid] = pid
    return vivos


@bp_agentes.route('/api/panel/agentes')
def api_panel_agentes():
    """Pulso del panel: por agente, config + contadores + PROCESO vivo."""
    cfg = leer_config_agentes()
    vivos = _procesos_vivos()
    out = []
    for aid in sorted(cfg.get('agentes', {}).keys()):
        a = resolver_agente(cfg, aid)
        info = {
            'id': aid,
            'nombre': a.get('name', aid),
            'rol': a.get('_desc', ''),
            'carpeta': os.path.basename(carpeta_de(a)),
            'persona': os.path.exists(os.path.join(carpeta_de(a), 'persona.md')),
            'pid': vivos.get(aid),
            'vivo': aid in vivos,
            'memoria': 0, 'creencias': 0, 'lecciones': 0,
        }
        try:
            cer = cerebro_de(a)
            if cer:
                info['memoria'], info['creencias'], info['lecciones'] = contadores_de(cer)
        except Exception:
            pass
        out.append(info)
    return jsonify({
        'agentes': out,
        'total': len(out),
        'vivos': sum(1 for x in out if x['vivo']),
    })


@bp_agentes.route('/api/panel/pizarra_resumen')
def api_panel_pizarra_resumen():
    """Resumen ligero de pizarras (para la cabecera del panel)."""
    from api.pizarra import _rutas_pizarra, _leer, _chat_de_ruta
    resumen = []
    for ruta in _rutas_pizarra():
        lineas = _leer(ruta)
        resumen.append({'chat': _chat_de_ruta(ruta), 'lineas': len(lineas),
                        'ultimo_ts': lineas[-1].get('ts') if lineas else None})
    return jsonify({'pizarras': resumen})


# ---------------------------------------------------------------------------
# Traducción IA <-> humano (modo del chat)
# ---------------------------------------------------------------------------
# El switch "modo humano" del frontend necesita convertir el TEXTO mostrado:
#  - modo 'humano': del idioma de los agentes (jerga técnica: daemons, tokens,
#    blueprints, pizarra...) a castellano claro que cualquiera entiende.
#  - modo 'ia'    : al revés, para escribirles a los agentes en su jerga.
# Usa el LLM REAL del agente (config.llm_de_agente); no es una simulación.

TRAD_SYS = {
    'humano': (
        "Eres un traductor experto. Recibes un texto escrito por agentes de IA "
        "del proyecto NotherClass en su 'idioma de agentes' (jerga técnica: "
        "daemons, pids, tokens, blueprints, pizarra, endpoints, LLM...). "
        "Reescríbelo en castellano claro y natural para un HUMANO no técnico: "
        "misma información y mismo tono, pero sin jerga. Explica entre "
        "paréntesis cualquier término imprescindible. Conserva nombres propios "
        "de archivos o herramientas, pero acompáñalos de una aclaración breve. "
        "Devuelve SOLO la traducción, sin preámbulos ni comillas."),
    'ia': (
        "Eres un traductor experto. Recibes un texto en castellano humano y "
        "debes reescribirlo en el 'idioma de agentes' del proyecto NotherClass: "
        "conciso y técnico, usando términos como daemon, pid, token, endpoint, "
        "blueprint, pizarra, LLM, recall, etc. donde encajen. Misma "
        "información, sin adornos. Devuelve SOLO la traducción, sin preámbulos "
        "ni comillas."),
}


def _trad_llm(llm, modo, texto):
    """Llamada OpenAI-compatible (GLM/DeepSeek) para traducir. (texto, modelo)."""
    sysp = TRAD_SYS.get(modo, TRAD_SYS['humano'])
    cuerpo = {
        'model': llm['modelo'],
        'messages': [
            {'role': 'system', 'content': sysp},
            {'role': 'user', 'content': texto},
        ],
        'stream': False,
    }
    if llm.get('reasoning_effort'):
        cuerpo['reasoning_effort'] = llm['reasoning_effort']
    r = requests.post(llm['base_url'], json=cuerpo, timeout=120,
                      headers={'Authorization': f"Bearer {llm['api_key']}"})
    r.raise_for_status()
    d = r.json()
    ch = d.get('choices') or [{}]
    msg = ch[0].get('message', {}) if ch else {}
    txt = (msg.get('content') or '').strip()
    if not txt and msg.get('reasoning_content'):
        txt = msg['reasoning_content'].strip()
    return txt, d.get('model', llm['modelo'])


@bp_agentes.route('/api/idioma/<aid>', methods=['POST'])
def api_idioma(aid):
    """Traduce un texto mostrado entre el idioma IA y el humano.

    POST /api/idioma/<aid>  body: {"texto": "...", "modo": "humano"|"ia"}
    Devuelve {"texto": <traducido>, "modo": ..., "modelo": ...}.
    """
    cfg = leer_config_agentes()
    if aid not in cfg.get('agentes', {}):
        return jsonify({'error': 'agente desconocido'}), 404
    a = resolver_agente(cfg, aid)
    payload = request.get_json(silent=True) or {}
    texto = (payload.get('texto') or '').strip()
    modo = 'ia' if payload.get('modo') == 'ia' else 'humano'
    if not texto:
        return jsonify({'error': 'texto vacío'}), 400
    try:
        llm = llm_de_agente(a)
    except Exception as e:
        return jsonify({'error': f'llm: {e}'}), 500
    if not llm.get('api_key') or not llm.get('base_url'):
        return jsonify({'error': 'agente sin LLM configurado'}), 500
    try:
        resp, modelo = _trad_llm(llm, modo, texto)
    except Exception as e:
        return jsonify({'error': f'llm: {e}'}), 502
    return jsonify({'texto': resp, 'modo': modo, 'modelo': modelo})


# ---------------------------------------------------------------------------
# MENÚ ROBUSTO backend-driven + entradas nuevas (JokerV2)
# ---------------------------------------------------------------------------
# Idea: el front NO cablea nada. Pide GET /api/menu y pinta tal cual el árbol
# que devolvemos. Así el menú crece/crece sin tocar HTML ni JS, y ningún dato
# sensible viaja al cliente (los endpoints devuelven estado saneado).

# Puertos/pistas de la instalación (los del proyecto NotherClass).
PUERTO_CONSOLA = 5000


def _arbol_menu():
    """Árbol del menú de la superconsola. Contrato estable con el front.

    Cada entrada: id, icono, titulo, y bien 'vista' (la pinta el front con
    sus propios widgets) o 'endpoint' (GET listo para volcar). 'sub' = hijas.
    """
    return [
        {'id': 'chat', 'icono': '💬', 'titulo': 'Chat con agentes',
         'vista': 'chat',
         'endpoint_pista': 'POST /api/chat/<aid> {texto,modo}',
         'historial': 'GET /api/chat/<aid>/historial'},
        {'id': 'persona', 'icono': '👤', 'titulo': 'Persona',
         'endpoint': '/api/memoria/<aid>?tipo=persona',
         'nota': 'El personaje/base de cada agente (persona.md).'},
        {'id': 'memoria', 'icono': '🧠', 'titulo': 'Memoria',
         'endpoint': '/api/memoria/<aid>?tipo=beliefs&page=1',
         'sub': [
             {'id': 'beliefs', 'icono': '🧩', 'titulo': 'Creencias',
              'endpoint': '/api/memoria/<aid>?tipo=beliefs&page=1'},
             {'id': 'chunks', 'icono': '📚', 'titulo': 'Recuerdos',
              'endpoint': '/api/memoria/<aid>?tipo=chunks&page=1'},
             {'id': 'lecciones', 'icono': '🎓', 'titulo': 'Lecciones',
              'endpoint': '/api/memoria/<aid>?tipo=lessons&page=1'},
         ]},
        {'id': 'pizarra', 'icono': '📋', 'titulo': 'Pizarra',
         'endpoint': '/api/pizarra',
         'sub': [
             {'id': 'pizarra_resumen', 'icono': '📑', 'titulo': 'Resumen',
              'endpoint': '/api/panel/pizarra_resumen'},
         ],
         'nota': 'Bus: /api/pizarra?chat=<id>&limit=40'},
        {'id': 'agentes', 'icono': '🤖', 'titulo': 'Agentes',
         'endpoint': '/api/panel/agentes',
         'nota': 'Pulso: persona, memoria, creencias, lecciones, pid.'},
        {'id': 'estado', 'icono': '📊', 'titulo': 'Estado del sistema',
         'endpoint': '/api/estado',
         'nota': 'Salud de consola, pizarras y daemons.'},
        {'id': 'metricas', 'icono': '📈', 'titulo': 'Métricas',
         'endpoint': '/api/metricas',
         'nota': 'Uso por agente: memoria, creencias, lecciones.'},
        {'id': 'config', 'icono': '⚙️', 'titulo': 'Sistema',
         'endpoint': '/api/config',
         'nota': 'Config saneada: NUNCA expone tokens ni claves.'},
        {'id': 'traducir', 'icono': '🌐', 'titulo': 'Traducir',
         'endpoint': 'POST /api/idioma/<aid> {texto,modo}',
         'vista': 'traducir'},
    ]


def _resolver_placeholders(obj, aid):
    """Sustituye el placeholder literal '<aid>' por el agente elegido.

    Recorre el árbol (dicts, listas, strings) y cambia '<aid>' por `aid` en
    cualquier endpoint/ruta. Así el front puede hacer fetch directo del
    endpoint devuelto, sin tener que reemplazar nada por su cuenta.
    """
    if aid is None:
        return obj
    if isinstance(obj, str):
        return obj.replace('<aid>', str(aid))
    if isinstance(obj, list):
        return [_resolver_placeholders(x, aid) for x in obj]
    if isinstance(obj, dict):
        return {k: _resolver_placeholders(v, aid) for k, v in obj.items()}
    return obj


@bp_agentes.route('/api/menu')
def api_menu():
    """Árbol del menú de la superconsola, listo para que el front lo pinte.

    GET /api/menu            -> endpoints con el placeholder '<aid>' (compat).
    GET /api/menu?aid=<id>   -> endpoints YA resueltos a ese agente, de modo
                                que el front puede hacer fetch directo.

    Si no se pasa 'aid', se resuelve al primer agente disponible del config.
    """
    cfg = leer_config_agentes()
    agentes_ids = sorted(cfg.get('agentes', {}).keys())
    defecto = None
    if agentes_ids:
        vivos = _procesos_vivos()
        # prefiere un agente VIVO; si ninguno, el primero por orden alfabético
        defecto = next((a for a in agentes_ids if a in vivos), agentes_ids[0])
    aid = (request.args.get('aid') or '').strip() or defecto
    entradas = _resolver_placeholders(_arbol_menu(), aid)
    return jsonify({
        'titulo': 'NotherClass · Superconsola',
        'aid': aid,
        'entradas': entradas,
        'total': len(entradas),
    })


def _puerto_vivo(puerto, timeout=0.6):
    """¿Responde ese puerto local? Devuelve True/False (tolerante)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(('127.0.0.1', int(puerto))) == 0
    except Exception:
        return False
    finally:
        s.close()


@bp_agentes.route('/api/estado')
def api_estado():
    """Estado del sistema: consola, pizarras y daemons vivos."""
    vivos = _procesos_vivos()
    pizarras = []
    try:
        from api.pizarra import _rutas_pizarra, _leer, _chat_de_ruta
        for ruta in _rutas_pizarra():
            lineas = _leer(ruta)
            pizarras.append({'chat': _chat_de_ruta(ruta), 'lineas': len(lineas)})
    except Exception:
        pass
    return jsonify({
        'consola': {'puerto': PUERTO_CONSOLA, 'vivo': _puerto_vivo(PUERTO_CONSOLA)},
        'agentes_vivos': sorted(vivos.keys()),
        'total_vivos': len(vivos),
        'pizarras': pizarras,
    })


@bp_agentes.route('/api/metricas')
def api_metricas():
    """Métricas por agente: conteos de memoria/creencias/lecciones."""
    cfg = leer_config_agentes()
    out = []
    for aid in sorted(cfg.get('agentes', {}).keys()):
        a = resolver_agente(cfg, aid)
        mem = cre = lec = 0
        try:
            cer = cerebro_de(a)
            if cer:
                mem, cre, lec = contadores_de(cer)
        except Exception:
            pass
        out.append({'id': aid, 'nombre': a.get('name', aid),
                    'memoria': mem, 'creencias': cre, 'lecciones': lec})
    return jsonify({'agentes': out, 'total': len(out)})


@bp_agentes.route('/api/config')
def api_config():
    """Config SANEADA: qué hay configurado, sin exponer secretos.

    Nunca devolvemos tokens, api_key, base_url ni rutas absolutas sensibles:
    solo nombres y banderas (tiene_llm, tiene_token...).
    """
    cfg = leer_config_agentes()
    out = []
    for aid in sorted(cfg.get('agentes', {}).keys()):
        a = resolver_agente(cfg, aid)
        tiene_llm = False
        try:
            llm = llm_de_agente(a)
            tiene_llm = bool(llm.get('api_key') and llm.get('base_url'))
        except Exception:
            pass
        out.append({
            'id': aid,
            'nombre': a.get('name', aid),
            'carpeta': os.path.basename(carpeta_de(a)),
            'tiene_llm': tiene_llm,
            'tiene_token': bool(a.get('token') or a.get('telegram_token')),
        })
    return jsonify({
        'agentes': out,
        'total': len(out),
        'nota': 'Saneado: no se exponen tokens, claves ni rutas absolutas.',
    })
