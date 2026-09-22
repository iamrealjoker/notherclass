# -*- coding: utf-8 -*-
"""voz.py — Blueprint de VOZ para el chat de la consola (JokerV2).

Reutiliza EXACTAMENTE el mismo motor que usan los agentes para oír y hablar,
NO la Web Speech API del navegador:

  - STT: agent/core/audio.py -> transcribe()  (faster-whisper venv311 / whisper.cpp)
  - TTS: agent/core/audio.py -> tts()          (piper, voz es_ES-davefx-medium)

Endpoints:
  POST /api/voz/<aid>/stt   multipart 'audio' (webm/ogg/wav/mp4)
                            -> {"texto": "...", "agente": aid}
  POST /api/voz/<aid>/tts   JSON {"texto": "..."}
                            -> audio/wav (bytes de piper)

Todo el trabajo pesado ocurre en el SERVIDOR (0 tokens de nube).
"""
import io
import os
import sys
import tempfile
import time

from flask import Blueprint, jsonify, request, send_file

# El motor de voz vive en agent/. Lo añadimos al path para importarlo tal cual.
AGENT_DIR = os.environ.get("TW_AGENT_DIR", "/app/notherclass/agent")
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from core import audio  # noqa: E402  (agent/core/audio.py)

from config import leer_config_agentes  # noqa: E402

bp = Blueprint('voz', __name__)

MAX_TTS_CHARS = 1500

# Audios de chat guardados en disco (reproducibles más tarde, como en
# Telegram). Sirve GET /api/voz/audio/<aid>/<nombre>.
DIR_VOZ = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '..', 'datos', 'voz')


def _dir_de(aid):
    d = os.path.abspath(os.path.join(DIR_VOZ, aid))
    os.makedirs(d, exist_ok=True)
    return d


def guardar_audio(aid, data, ext='wav'):
    """Guarda un WAV de chat en datos/voz/<aid>/ y devuelve su nombre."""
    nombre = f"{int(time.time() * 1000)}_{os.getpid()}{ext}"
    with open(os.path.join(_dir_de(aid), nombre), 'wb') as fh:
        fh.write(data)
    return nombre


@bp.route('/api/voz/audio/<aid>/<nombre>')
def audio_guardado(aid, nombre):
    """Sirve un audio guardado (solo de la carpeta del propio agente)."""
    if '/' in nombre or '..' in nombre:
        return jsonify({'error': 'no encontrado'}), 404
    ruta = os.path.join(_dir_de(aid), nombre)
    if not os.path.isfile(ruta):
        return jsonify({'error': 'no encontrado'}), 404
    return send_file(ruta, mimetype='audio/wav', download_name=nombre)


def _agente_ok(aid):
    try:
        cfg = leer_config_agentes()
        # Agentes reales + servicios EXTRA con bot propio (traductor).
        # Excluye pseudo-agentes de infraestructura (web, consola, coordinador).
        EXCLUIR = {'web', 'consola', 'coordinador', '_comentario'}
        return (aid in cfg.get('agentes', {})
                or (aid in cfg.get('servicios_extra', {})
                    and aid not in EXCLUIR))
    except Exception:
        # Si la config falla, no bloqueamos la voz (es utilidad local).
        return True


@bp.route('/api/voz/<aid>/stt', methods=['POST'])
def stt(aid):
    """Recibe audio del navegador y devuelve la transcripción (motor agentes)."""
    if not _agente_ok(aid):
        return jsonify({'error': 'agente desconocido'}), 404

    f = request.files.get('audio')
    if f is None:
        return jsonify({'error': "falta el campo 'audio'"}), 400

    tdir = tempfile.mkdtemp(prefix='tw_voz_')
    # El contenedor de entrada puede ser webm/ogg/mp4; ffmpeg lo detecta solo.
    ext = os.path.splitext(f.filename or '')[1] or '.webm'
    src = os.path.join(tdir, 'in' + ext)
    wav = os.path.join(tdir, 'in.wav')
    nombre = ''
    try:
        f.save(src)
        audio.oga_to_wav(src, wav)          # ffmpeg -> wav 16k mono
        texto = (audio.transcribe(wav) or '').strip()
        if texto:
            # Nota de voz del HUMANO persistida (como en Telegram): queda
            # reproducible en el chat aunque recargues la página.
            with open(wav, 'rb') as fh:
                nombre = guardar_audio(aid, fh.read(), '.wav')
    except Exception as e:  # noqa: BLE001
        return jsonify({'error': f'stt: {e}'}), 500
    finally:
        for p in (src, wav):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tdir)
        except OSError:
            pass

    if not texto:
        return jsonify({'texto': '', 'vacio': True}), 200
    return jsonify({'texto': texto, 'agente': aid, 'audio': nombre})


@bp.route('/api/voz/<aid>/tts', methods=['POST'])
def tts(aid):
    """Convierte texto en voz (piper local) y devuelve un WAV."""
    if not _agente_ok(aid):
        return jsonify({'error': 'agente desconocido'}), 404

    payload = request.get_json(silent=True) or {}
    texto = (payload.get('texto') or '').strip()
    if not texto:
        return jsonify({'error': 'texto vacío'}), 400
    texto = texto[:MAX_TTS_CHARS]

    # VOZ POR IDIOMA: el traductor contesta en el idioma CONTRARIO al que entra,
    # así que lee con la voz inglesa cuando el texto va en inglés (antes leía el
    # inglés con acento español). Si el front no manda 'idioma', se DETECTA por
    # el propio texto -> no hay que cambiar nada en el navegador.
    idioma = (payload.get('idioma') or '').strip().lower()
    if idioma not in ('es', 'en'):
        idioma = 'es'
        if aid == 'traductor':
            try:
                from api import lengua
            except Exception:  # noqa: BLE001
                lengua = None
            if lengua is not None:
                idioma = 'en' if not lengua.es_espanol(texto) else 'es'
    modelo = None
    if idioma == 'en':
        modelo = getattr(audio, 'PIPER_MODEL_EN', None)

    tdir = tempfile.mkdtemp(prefix='tw_voz_')
    wav = os.path.join(tdir, 'out.wav')
    try:
        audio.tts(texto, wav, modelo=modelo)
        if not os.path.exists(wav) or os.path.getsize(wav) == 0:
            return jsonify({'error': 'tts no generó audio'}), 500
        with open(wav, 'rb') as fh:
            data = fh.read()
    except Exception as e:  # noqa: BLE001
        return jsonify({'error': f'tts: {e}'}), 500
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
        try:
            os.rmdir(tdir)
        except OSError:
            pass

    # Se guarda SIEMPRE antes de devolverlo: el chat queda con el audio
    # persistido (como las notas de voz de Telegram) y reproducible luego.
    nombre = guardar_audio(aid, data, '.wav')
    resp = send_file(io.BytesIO(data), mimetype='audio/wav',
                     download_name='voz.wav')
    resp.headers['X-Audio-Nombre'] = nombre
    return resp