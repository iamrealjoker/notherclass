# -*- coding: utf-8 -*-
"""chat.py — Blueprint de chat con agentes (consola).

Hablar con un agente = llamada REAL al LLM (GLM/DeepSeek) con su persona +
resumen de su cerebro como contexto. Historial propio de la CONSOLA en
consola/datos/consola.sqlite3 (no toca la memoria del agente).

Modo del switch:
  - 'agente' : el agente responde en su idioma natural (jerga técnica).
  - 'humano' : el MISMO agente responde en castellano claro (se añade
               MODO_HUMANO_EXTRA al system prompt).

El historial es ÚNICO por agente (NO separado por modo): cada mensaje guarda
en qué modo se generó. La traducción de lo YA mostrado la hace /api/idioma/<aid>
(lo usa el switch del frontend).

Lenguaje natural (2026-09-13): todas las respuestas llevan NATURAL_EXTRA en el
system prompt -> hablar como persona, sin markdown (nada de ** ni * ni listas
constantes) y sin decir los símbolos en voz alta.
"""
import json
import os

import requests
from flask import Blueprint, jsonify, request

from config import (leer_config_agentes, resolver_agente, carpeta_de,
                    llm_de_agente, resumen_cerebro)

import app_db

bp = Blueprint('chat', __name__)

MAX_HIST = 12

MODO_HUMANO_EXTRA = (
    "\n\nMODO HUMANO (importante): el lector es un HUMANO que no domina tu "
    "jerga técnica. Responde en castellano claro y sencillo: explica cualquier "
    "concepto técnico entre paréntesis, usa frases cortas y evita anglicismos "
    "innecesarios. Mismo contenido, misma sustancia, pero comprensible para "
    "cualquiera. Puedes conservar nombres propios de archivos o herramientas, "
    "pero acompáñalos siempre de una explicación breve.")


NATURAL_EXTRA = (
    "\n\nLENGUAJE NATURAL (muy importante): estás HABLANDO con una persona, "
    "no escribiendo un informe. Habla como en una conversación de voz natural: "
    "frases cortas, tono cercano y directo. PROHIBIDO usar formato markdown: "
    "nada de **negritas**, ni *asteriscos*, ni #, ni listas con guiones. "
    "NUNCA pronuncies ni nombres símbolos: no digas 'asterisco asterisco', "
    "'diagonal', 'barra' ni similares. Si necesitarías una lista, dilo en "
    "frase seguida: 'primero... luego...'. Responde en español.")


def _modo_de(payload):
    m = (payload or {}).get('modo', 'agente')
    return 'humano' if m == 'humano' else 'agente'


def _persona_de(a):
    p = os.path.join(carpeta_de(a), 'persona.md')
    if os.path.exists(p):
        return open(p, encoding='utf-8').read().strip()
    return a.get('_desc', a.get('name', ''))


def _turno_agente(puerto, texto, timeout=1800):
    """Pide un turno REAL al agente (run.py) por su API local.

    Devuelve el dict de respuesta o **None** si el agente no está / no responde
    (entonces el chat de la web cae al LLM de siempre, degradado pero funcional).
    """
    import json as _json
    import urllib.request as _u
    try:
        req = _u.Request(f"http://127.0.0.1:{int(puerto)}/turn",
                         data=_json.dumps({"texto": texto}).encode("utf-8"),
                         headers={"Content-Type": "application/json"}, method="POST")
        with _u.urlopen(req, timeout=timeout) as r:
            return _json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _llamar_llm(llm, mensajes, temperature=None):
    """Llamada OpenAI-compatible (GLM/DeepSeek). Devuelve (texto, modelo)."""
    cuerpo = {'model': llm['modelo'], 'messages': mensajes, 'stream': False}
    if temperature is not None:
        cuerpo['temperature'] = temperature
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


def _historial(aid, n):
    """Historial UNIFICADO del agente (todos los modos), orden cronológico.

    Devuelve [{rol:'humano'|'agente', texto, modelo, modo}], que es justo lo
    que espera el frontend (`d.historial[].rol === 'humano'`).
    """
    with app_db.conectar() as c:
        filas = c.execute(
            "SELECT rol, texto, modelo, modo, audio, trazado FROM conversaciones "
            "WHERE agente_id=? ORDER BY id DESC LIMIT ?", (aid, n)).fetchall()
    filas = list(reversed(filas))
    out = []
    for r in filas:
        try:
            tr = json.loads(r['trazado'] or '[]')
        except Exception:  # noqa: BLE001
            tr = []
        out.append({'rol': ('humano' if r['rol'] == 'humano' else 'agente'),
                    'texto': r['texto'],
                    'modelo': r['modelo'] or '',
                    'modo': r['modo'] or 'agente',
                    'audio': r['audio'] or '',
                    'trazado': tr})
    return out


@bp.route('/api/chat/<aid>', methods=['POST'])
def chat(aid):
    cfg = leer_config_agentes()
    if aid not in cfg['agentes']:
        # servicios_extra (p.ej. 'traductor'): chat genérico con nombre propio
        ext = cfg.get('servicios_extra', {}).get(aid)
        if not ext:
            return jsonify({'error': 'agente desconocido'}), 404
        a = {'name': ext.get('name', aid), 'carpeta': ''}
    else:
        a = resolver_agente(cfg, aid)
    payload = request.get_json(silent=True) or {}
    texto = (payload.get('texto') or '').strip()
    if not texto:
        return jsonify({'error': 'texto vacío'}), 400
    modo = _modo_de(payload)
    try:
        llm = llm_de_agente(a)
    except Exception as e:
        return jsonify({'error': f'llm: {e}'}), 500
    if not llm.get('api_key') or not llm.get('base_url'):
        # servicios_extra sin LLM propio: usan el LLM por defecto de la config
        dflt = llm_de_agente(cfg.get('_defaults', {}))
        if not dflt.get('api_key') or not dflt.get('base_url'):
            return jsonify({'error': 'agente sin LLM configurado'}), 500
        llm = dflt
        a = dict(a, llm_provider=dflt['proveedor'], llm_model=dflt['modelo'],
                 llm_base_url=dflt['base_url'], llm_api_key=dflt['api_key'])

    # ── TRADUCTOR (servicio 'traductor') ────────────────────────────────────
    # Antes se trataba como un chat genérico (el LLM contestaba lo que quería).
    # Ahora se comporta EXACTAMENTE igual que el bot de Telegram "Lengua":
    # detecta el idioma y devuelve la traducción a la INVERSA. El TTS usa la voz
    # del idioma de salida (lo resuelve /api/voz/<aid>/tts).
    if aid == 'traductor':
        app_db.db_guardar(aid, 'humano', texto, '', modo,
                          (payload.get('audio_humano') or '').strip())
        try:
            from api import lengua
        except Exception:  # noqa: BLE001  (consola/api en el path)
            import lengua  # type: ignore
        tr, era_es = lengua.traducir(
            texto, lambda msgs: _llamar_llm(llm, msgs, temperature=0.0)[0])
        if not tr:
            return jsonify({'error': 'no pude traducir'}), 502
        idioma = lengua.idioma_salida(era_es)
        app_db.db_guardar(aid, 'agente', tr, llm['modelo'], modo, '')
        return jsonify({'respuesta': tr, 'modelo': llm['modelo'], 'modo': modo,
                        'idioma': idioma,
                        'direccion': 'es->en' if era_es else 'en->es'})

    # ── AGENTE REAL (runtime de run.py) ─────────────────────────────────────
    # El chat de la web debe ser el MISMO agente que el de Telegram: mismas
    # herramientas y memoria. run.py expone un API local (127.0.0.1) en su
    # 'http_port' (configAgentes.json). Si el agente no responde -> chat LLM.
    _puerto = (cfg['agentes'].get(aid) or {}).get('http_port')
    if _puerto:
        _r = _turno_agente(_puerto, texto)
        if _r is None:
            # Degradado: se avisa en la propia respuesta (mejor que mentir).
            _r = None
        elif _r.get('ok'):
            resp = _r.get('respuesta') or ''
            # El detalle de lo ejecutado YA NO va dentro de la respuesta (quedaba
            # feo y el TTS lo leia). Va aparte, en 'trazado', y la web lo pinta en
            # un desplegable "ejecutado" que puedes abrir cuando quieras.
            app_db.db_guardar(aid, 'humano', texto, '', modo,
                              (payload.get('audio_humano') or '').strip())
            app_db.db_guardar(aid, 'agente', resp, 'agente real', modo, '',
                              _r.get('trazado') or [])
            return jsonify({'respuesta': resp, 'modelo': 'agente real', 'modo': modo,
                            'herramientas': _r.get('herramientas') or [],
                            'trazado': _r.get('trazado') or [],
                            'seg': _r.get('seg'), 'coste': _r.get('coste')})
        elif _r.get('error'):
            app_db.db_guardar(aid, 'humano', texto, '', modo,
                              (payload.get('audio_humano') or '').strip())
            resp = f"⚠️ El agente falló: {_r['error']}"
            app_db.db_guardar(aid, 'agente', resp, '', modo, '')
            return jsonify({'respuesta': resp, 'modelo': 'agente real', 'modo': modo})

    es_servicio = aid in cfg.get('servicios_extra', {})
    sysp = (f"Eres {a.get('name', aid)}, asistente del proyecto NotherClass.\n\n"
            f"PERSONA:\n{_persona_de(a)}\n\n")
    if not es_servicio:
        sysp += ("Tu memoria (creencias y lecciones más relevantes):\n"
                 f"{resumen_cerebro(a)}\n\n")
    if modo == 'humano':
        sysp += MODO_HUMANO_EXTRA
    sysp += NATURAL_EXTRA
    mensajes = [{'role': 'system', 'content': sysp}]
    for m in _historial(aid, MAX_HIST):
        mensajes.append({'role': 'user' if m['rol'] == 'humano'
                         else 'assistant', 'content': m['texto']})
    mensajes.append({'role': 'user', 'content': texto})

    app_db.db_guardar(aid, 'humano', texto, '', modo,
                      (payload.get('audio_humano') or '').strip())
    try:
        resp, modelo = _llamar_llm(llm, mensajes)
    except Exception as e:
        return jsonify({'error': f'llm: {e}'}), 502
    # '__TTS__' es solo un marcador del front (el wav real lo asocia después
    # POST /api/chat/<aid>/audio); guardarlo literal rompería el botón ▶ voz.
    _resp_audio = (payload.get('audio_respuesta') or '').strip()
    if _resp_audio == '__TTS__':
        _resp_audio = ''
    app_db.db_guardar(aid, 'agente', resp, modelo, modo, _resp_audio)
    return jsonify({'respuesta': resp, 'modelo': modelo, 'modo': modo})


@bp.route('/api/chat/<aid>/audio', methods=['POST'])
def asignar_audio(aid):
    """Asigna un nombre de audio ya persistido a un mensaje del historial.

    {indice:-1} = último mensaje del agente; {nombre:'<archivo>.wav'}.
    Se usa tras el TTS para que el botón ▶ voz sobreviva a la recarga.
    """
    cfg = leer_config_agentes()
    if aid not in cfg['agentes'] and aid not in cfg.get('servicios_extra', {}):
        return jsonify({'error': 'agente desconocido'}), 404
    payload = request.get_json(silent=True) or {}
    nombre = (payload.get('nombre') or '').strip()
    if '/' in nombre or '..' in nombre:
        return jsonify({'error': 'nombre inválido'}), 400
    indice = payload.get('indice', -1)
    with app_db.conectar() as c:
        fila = c.execute(
            "SELECT id FROM conversaciones WHERE agente_id=? AND rol='agente' "
            "ORDER BY id DESC LIMIT 1 OFFSET ?", (aid, -indice - 1)).fetchone()
        if not fila:
            return jsonify({'error': 'mensaje no encontrado'}), 404
        c.execute("UPDATE conversaciones SET audio=? WHERE id=?",
                  (nombre, fila['id']))
    return jsonify({'ok': True, 'audio': nombre})


@bp.route('/api/chat/<aid>/estado')
def estado(aid):
    """Estado del turno EN CURSO del agente (para el mensaje efímero de la web).

    Devuelve {'activo': bool, 'texto': '...', 'seg': n, 'pasos': n}. La web lo
    pintar EN VIVO mientras espera y la burbuja se reemplaza por la respuesta
    final (efímera, igual que Telegram). Si el agente no tiene API local o no
    responde, devuelve activo=False (el chat sigue funcionando igual).
    """
    import json as _json
    import urllib.request as _u
    cfg = leer_config_agentes()
    if aid not in cfg['agentes'] and aid not in cfg.get('servicios_extra', {}):
        return jsonify({'error': 'agente desconocido'}), 404
    puerto = (cfg['agentes'].get(aid) or {}).get('http_port')
    if not puerto:
        return jsonify({'activo': False})
    try:
        with _u.urlopen(f"http://127.0.0.1:{int(puerto)}/estado", timeout=3) as r:
            return jsonify(_json.loads(r.read().decode("utf-8")))
    except Exception:  # noqa: BLE001
        return jsonify({'activo': False})


@bp.route('/api/chat/<aid>/historial')
def historial(aid):
    cfg = leer_config_agentes()
    if aid not in cfg['agentes'] and aid not in cfg.get('servicios_extra', {}):
        return jsonify({'error': 'agente desconocido'}), 404
    return jsonify({'historial': _historial(aid, 200)})
