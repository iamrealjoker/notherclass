# -*- coding: utf-8 -*-
"""
app.py — CONSOLA DE AGENTES (tipo OpenClaw) para NotherClass.

Toda la lógica vive en el backend. El navegador solo recibe HTML/JSON.
Puerto: 5000 (lo pide el humano: único puerto abierto del contenedor).
"""
import json
import os
import sqlite3

import requests
from flask import Flask, jsonify, render_template, request

from config import (leer_config_agentes, resolver_agente, carpeta_de,
                    llm_de_agente, cerebro_de, contadores_de,
                    resumen_cerebro, tail_log)
from api.chat import bp as bp_chat            # chat.py exporta 'bp' (V1)
from api.memoria import bp as bp_memoria       # JokerV2
from api.pizarra import bp_pizarra             # JokerV2
from api.agentes import bp_agentes             # JokerV2
from api.voz import bp as bp_voz                # JokerV2 (STT/TTS chat)

RUTA = os.path.dirname(os.path.abspath(__file__))

# Pseudo-agentes de infraestructura: no son chatables ni se listan como tales.
NO_CHAT = {'consola', 'coordinador', '_comentario'}
DATOS = os.path.join(RUTA, 'datos')
DB = os.path.join(DATOS, 'consola.sqlite3')

app = Flask(__name__)
# ── HOT-RELOAD ──────────────────────────────────────────────────────────
# Releer plantillas y estáticos del disco en cada petición: editar
# templates/*.html o static/*.js y basta con recargar el navegador (F5),
# SIN reiniciar el daemon. Los .py se recargan solos por el reloader
# de Werkzeug (ver __main__).
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.jinja_env.auto_reload = True
app.register_blueprint(bp_chat)
app.register_blueprint(bp_memoria)     # /api/memoria/<aid>
app.register_blueprint(bp_pizarra)     # /api/pizarra
app.register_blueprint(bp_agentes)     # /api/panel/agentes, /api/panel/pizarra_resumen
app.register_blueprint(bp_voz)         # /api/voz/<aid> (STT), /api/voz/<aid>/tts

def conectar():
    os.makedirs(DATOS, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def asegurar_esquema():
    with conectar() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS conversaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agente_id TEXT NOT NULL,
    rol TEXT NOT NULL,
    texto TEXT NOT NULL,
    modelo TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv ON conversaciones(agente_id, id);
""")


# ───────────────────────────────────────────────────────────── API: agentes
@app.route('/api/agentes')
def api_agentes():
    cfg = leer_config_agentes()
    out = []
    for aid in sorted(cfg['agentes'].keys()):
        a = resolver_agente(cfg, aid)
        info = {'id': aid, 'nombre': a.get('name', aid), 'rol': a.get('_desc', ''),
                'carpeta': a.get('carpeta', ''), 'llm': None, 'memoria': 0,
                'creencias': 0, 'lecciones': 0, 'vivo': False}
        try:
            m = llm_de_agente(a)
            info['llm'] = {'modelo': m['modelo'], 'proveedor': m['proveedor']}
        except Exception:
            pass
        try:
            cer = cerebro_de(a)
            if cer:
                info['memoria'], info['creencias'], info['lecciones'] = contadores_de(cer)
                info['vivo'] = True
        except Exception:
            pass
        out.append(info)
    # NO listar pseudo-agentes de infraestructura (sin chat propio).
    # servicios_extra (p.ej. traductor): aparecen en #sel_agente sin ser agentes.
    for sid in sorted(cfg.get('servicios_extra', {}).keys()):
        s = cfg['servicios_extra'][sid]
        if not isinstance(s, dict) or sid in NO_CHAT:
            continue
        out.append({'id': sid, 'nombre': s.get('name', sid),
                    'rol': s.get('_desc', 'servicio extra'), 'carpeta': '',
                    'llm': None, 'memoria': 0, 'creencias': 0,
                    'lecciones': 0, 'vivo': True, 'extra': True})
    return jsonify(out)


@app.route('/api/agente/<aid>')
def api_agente(aid):
    cfg = leer_config_agentes()
    if (aid not in cfg['agentes'] and not (aid in cfg.get('servicios_extra', {})
                                           and aid not in NO_CHAT)):
        return jsonify({'error': 'agente desconocido'}), 404
    if aid in cfg['agentes']:
        a = resolver_agente(cfg, aid)
        es_extra = False
    else:
        s = cfg['servicios_extra'][aid]
        a = {'name': s.get('name', aid), '_desc': s.get('_desc', 'servicio extra'),
             'carpeta': ''}
        es_extra = True
        det = {'id': aid, 'nombre': a.get('name', aid), 'rol': a.get('_desc', ''),
           'carpeta': a.get('carpeta', ''), 'llm': None,
           'persona': '', 'creencias': [], 'lecciones': [], 'chunks': [],
           'log': [], 'errores': []}
    if es_extra:
        det['extra'] = True
        return jsonify(det)  # los extras no tienen cerebro ni logs propios
    try:
        m = llm_de_agente(a)
        # NUNCA mandamos la api_key al navegador.
        det['llm'] = {'proveedor': m['proveedor'], 'modelo': m['modelo'],
                      'base_url': m['base_url'], 'reasoning_effort': m['reasoning_effort']}
    except Exception as e:
        det['errores'].append(f'llm: {e}')
    try:
        p = os.path.join(carpeta_de(a), 'persona.md')
        det['persona'] = open(p, encoding='utf-8').read() if os.path.exists(p) else ''
    except Exception as e:
        det['errores'].append(f'persona: {e}')
    try:
        cer = cerebro_de(a)
        if cer:
            det['creencias'] = [dict(r) for r in cer.execute(
                'SELECT key, value, confidence FROM beliefs '
                'ORDER BY confidence DESC LIMIT 50')]
            det['lecciones'] = [dict(r) for r in cer.execute(
                'SELECT kind, title, outcome FROM lessons '
                'ORDER BY rowid DESC LIMIT 30').fetchall()]
            det['chunks'] = [dict(r) for r in cer.execute(
                'SELECT * FROM chunks ORDER BY rowid DESC LIMIT 12')]
    except Exception as e:
        det['errores'].append(f'cerebro: {e}')
    try:
        det['log'] = tail_log(a, 60)
    except Exception as e:
        det['errores'].append(f'log: {e}')
    return jsonify(det)


# ───────────────────────────────────────────────────────────── Vista única
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    return jsonify({'ok': True, 'servicio': 'consola-agentes'})


@app.route('/ca.crt')
def ca_crt():
    """Descarga la CA local para INSTALARLA en el móvil (así https://<ip>:5000 es
    de confianza y el micrófono funciona). Abre en el móvil:  http://<ip>:5000/ca.crt
    """
    import os as _os
    from flask import send_file as _send, abort as _abort
    ruta = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                         'certs', 'ca.crt')
    if not _os.path.exists(ruta):
        _abort(404)
    return _send(ruta, mimetype='application/x-x509-ca-cert',
                 download_name='notherclass-ca.crt', as_attachment=True)


if __name__ == '__main__':
    asegurar_esquema()
    # use_reloader=False: el RELOADER de Flask lanza un SEGUNDO proceso y el watcher
    # (que ahora SÍ supervisa la consola) mata duplicados → la consola se caía en
    # bucle. La supervisión/relanzado ya la hace el watcher del supervisor.
    # debug=False para NO exponer el depurador Werkzeug por la red (puerto 5000).
    # TW_CONSOLA_PUERTO: permite servirla en un puerto INTERNO (5051) detrás del
    # multiplexor HTTP/HTTPS que escucha en el 5000 (consola/proxy5000.py).
    _puerto = int(os.environ.get('TW_CONSOLA_PUERTO', '5000'))
    app.run(host='0.0.0.0', port=_puerto, debug=False, use_reloader=False)
