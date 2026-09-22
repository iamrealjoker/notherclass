"""
core/audio.py — STT + TTS LOCAL (0 tokens de la nube) para el agente.

Usa binarios ya instalados (no tocan Python):
  - whisper.cpp   (STT):   /opt/whisper.cpp/build/bin/whisper-cli
  - piper         (TTS):   /opt/piper/piper  (voz natural, necesita LD_LIBRARY_PATH=/opt/piper)
  - ffmpeg                 (conversión oga<->wav<->ogg)

Modelos:
  - STT:   /opt/whisper.cpp/models/ggml-base.bin
  - TTS es: agent/audio_models/es_ES-davefx-medium.onnx

Se invocan por subprocess; por eso funcionan igual en Python 3.8.
"""
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)                       # .../agent

WHISPER_BIN = os.environ.get("TW_WHISPER_BIN", "/opt/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL = os.environ.get("TW_WHISPER_MODEL", "/opt/whisper.cpp/models/ggml-base.bin")
PIPER_BIN = os.environ.get("TW_PIPER_BIN", "/opt/piper/piper")
PIPER_DIR = os.environ.get("TW_PIPER_DIR", "/opt/piper")
PIPER_MODEL = os.environ.get(
    "TW_PIPER_MODEL", os.path.join(AGENT, "audio_models", "es_ES-davefx-medium.onnx"))
# Voz INGLESA: la usa el traductor (y el TTS cuando detecta que el texto va en
# inglés), para que no lea inglés con acento español.
PIPER_MODEL_EN = os.environ.get(
    "TW_PIPER_MODEL_EN", os.path.join(AGENT, "audio_models", "en_US-lessac-medium.onnx"))
STT_LANG = os.environ.get("TW_STT_LANG", "es")

# STT rápido: faster-whisper en el venv Python 3.11 (si existe), si no, whisper.cpp.
FW_PY = os.environ.get("TW_FW_PY", os.path.join(AGENT, "venv311", "bin", "python"))
FW_SCRIPT = os.environ.get("TW_FW_SCRIPT", os.path.join(AGENT, "fw_stt.py"))
FW_SIZE = os.environ.get("TW_FW_SIZE", "base")


def _run(cmd, inp=None):
    env = dict(os.environ)
    if PIPER_DIR:
        env["LD_LIBRARY_PATH"] = PIPER_DIR + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        env["PIPER_DATA_DIR"] = PIPER_DIR
    return subprocess.run(cmd, input=inp, capture_output=True, env=env)


def oga_to_wav(src, dst):
    """Telegram da .ogg/.oga opus -> wav 16kHz mono (lo que necesita whisper)."""
    _run(["ffmpeg", "-y", "-i", src, "-ar", "16000", "-ac", "1",
          "-c:a", "pcm_s16le", dst])


def wav_to_ogg(src, dst):
    """wav -> ogg/opus (formato de las notas de voz de Telegram)."""
    _run(["ffmpeg", "-y", "-i", src, "-c:a", "libopus", "-b:a", "20000", dst])


_AVISO_RES = {"ts": 0.0}


def _aviso_residente(motivo):
    """⚠️ Deja RASTRO cuando el residente STT no responde.

    Sin esto, la web y Telegram volverían al modo LENTO en silencio (recargando el
    modelo en cada nota) y nadie se enteraría. 1 aviso por minuto como máximo.
    """
    import time
    ahora = time.time()
    if ahora - _AVISO_RES["ts"] < 60:
        return
    _AVISO_RES["ts"] = ahora
    print(f"[audio] ⚠️ STT residente NO disponible ({motivo}) → FALLBACK al modo "
          "LENTO (recarga el modelo en cada nota). Mira 'reiniciar_agentes.py "
          "--estado'.", flush=True)


def _stt_sock(wav_path, lang=None):
    """Pide el texto al servicio residente fw_stt --servir (modelo precargado).

    El residente escucha por HTTP en 127.0.0.1:5077 (POST /transcribir con el
    wav en el cuerpo). FW_STT_URL permite cambiarlo sin tocar código.

    Devuelve (ok, texto). `ok=True` significa "el residente SÍ transcribió": el
    texto puede venir VACÍO (audio sin voz) y eso NO es un fallo, así que NO se
    debe caer al modo lento (antes un 200 vacío disparaba whisper.cpp en cada
    nota y con -t 8 en una CPU de 1 núcleo ponía el load a 8)."""
    import http.client
    url = os.environ.get("FW_STT_URL", "127.0.0.1:5077")
    host, _, port = url.partition(":")
    try:
        c = http.client.HTTPConnection(host, int(port or 5077), timeout=30)
        with open(wav_path, "rb") as f:
            cuerpo = f.read()
        c.request("POST", "/transcribir", body=cuerpo,
                  headers={"Content-Type": "application/octet-stream"})
        r = c.getresponse()
        txt = r.read().decode("utf-8", "replace").strip()
        c.close()
        if r.status == 200 and not txt.startswith("error"):
            return True, txt
        _aviso_residente(f"el residente respondió HTTP {r.status}")
    except Exception as e:  # noqa: BLE001
        _aviso_residente(str(e)[:80])
    return False, ""


# ── audio SIN VOZ: whisper devuelve marcadores como "[Música]", "(aplausos)",
# "[BLANK_AUDIO]"... No es una transcripción: si se manda al agente, contesta a
# la nada y encima dispara el TTS (ida y vuelta de voz en bucle). Se filtra.
_NO_HABLA = re.compile(
    r"\[\s*(m[uú]sica|music|aplausos|applause|risas|laughter|silencio|silence|"
    r"blank_audio|inaudible|ruido|noise|sonido|sound)[^\]]*\]|"
    r"\(\s*(m[uú]sica|music|aplausos|applause|risas|laughter|silencio|silence)"
    r"[^)]*\)|\u266a+|\u266b+",
    re.I)


def limpiar_no_habla(texto):
    """Quita marcadores de audio sin voz. Devuelve '' si NO había voz real."""
    t = _NO_HABLA.sub(" ", texto or "")
    t = re.sub(r"[\s.,;:!?¡¿\-–—]+", " ", t).strip()
    return t


def transcribe(wav_path):
    """STT local. Usa faster-whisper (venv 3.11) si está; si no, whisper.cpp.

    Devuelve '' si el audio no tiene voz (solo música/ruido/silencio)."""
    # 0) servicio residente (modelo precargado, ~1-2s) si está vivo.
    #    Si responde 200 (aunque sea vacío) es la VERDAD: no hay que insistir.
    ok, txt = _stt_sock(wav_path)
    if ok:
        return limpiar_no_habla(txt)
    # 1) faster-whisper por subproceso (más lento: recarga el modelo)
    if os.path.exists(FW_PY) and os.path.exists(FW_SCRIPT):
        try:
            r = _run([FW_PY, FW_SCRIPT, wav_path])
            out = (r.stdout or b"").decode("utf-8", "replace").strip()
            if out:
                return limpiar_no_habla(out)
        except Exception:
            pass
    # 2) fallback whisper.cpp. -t = núcleos REALES de la máquina: en este server
    #    hay 1 CPU, así que -t 8 solo hacía que se pelearan los hilos (load 8).
    hilos = str(max(1, min(4, os.cpu_count() or 1)))
    outbase = os.path.splitext(wav_path)[0]
    _run([WHISPER_BIN, "-m", WHISPER_MODEL, "-f", wav_path,
          "-l", STT_LANG, "-t", hilos, "-otxt", "-of", outbase])
    txtfile = outbase + ".txt"
    if os.path.exists(txtfile):
        try:
            with open(txtfile, "r", encoding="utf-8") as fh:
                text = fh.read().strip()
        finally:
            os.remove(txtfile)
        return limpiar_no_habla(text)
    return ""


# ─────────────────────── VOZ HUMANA: limpiar lo que se lee ──────────────────
# Piper pronuncia TODO tal cual: los asteriscos de markdown, los emojis, las
# URLs o los guiones de lista suenan fatal ("asterisco asterisco", "almohadilla").
# `humanizar()` deja el texto como lo diría una persona. Se aplica SIEMPRE en tts().
_EMOJIS = re.compile(
    "[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F\u200d]")
_MD_NEGRITA = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_MD_CURSIVA = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_MD_TACHADO = re.compile(r"~~(.+?)~~", re.S)


def humanizar(texto):
    """Convierte markdown/símbolos en texto que SUENA BIEN leído en voz alta."""
    t = texto or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)           # bloques de código
    t = re.sub(r"`([^`]*)`", r"\1", t)                     # `código` inline
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)            # imágenes markdown
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)         # enlaces -> su texto
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)           # URLs
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)    # títulos #
    t = re.sub(r"^\s*[-*•·]\s+", "", t, flags=re.M)        # viñetas
    t = re.sub(r"^\s*\d+[.)]\s+", "", t, flags=re.M)       # listas "1."
    t = re.sub(r"^\s*[-—=_*]{3,}\s*$", "", t, flags=re.M)  # separadores ---
    t = t.replace("|", " ")                                # tablas
    t = _MD_NEGRITA.sub(lambda m: m.group(1) or m.group(2), t)
    t = _MD_TACHADO.sub(r"\1", t)
    t = _MD_CURSIVA.sub(r"\1", t)
    t = t.replace("*", " ").replace("#", " ").replace("_", " ")
    t = _EMOJIS.sub(" ", t)
    # Comillas y parentesis/corchetes: que NO los lea en voz alta -> se quitan
    # (antes decia "comillas", "parentesis"... sonaba a robot).
    t = re.sub(r'["\'\u00ab\u00bb\u201c\u201d\u2018\u2019]', " ", t)
    t = re.sub(r"[()\[\]{}]", " ", t)
    # símbolos que piper lee mal -> palabras
    t = re.sub(r"\$\s*([\d.,]+)", r"\1 dólares", t)
    t = t.replace("€", " euros").replace("%", " por ciento")
    t = t.replace("→", ", ").replace("->", ", ").replace("=>", ", ")
    t = t.replace("+", " más ").replace("=", " igual a ").replace("&", " y ")
    t = t.replace("…", " ").replace("—", ", ").replace("–", ", ")
    # une líneas en frases (piper pausa con la puntuación, no con los saltos)
    partes = []
    for linea in (l.strip() for l in t.splitlines()):
        if not linea:
            continue
        if partes and partes[-1][-1] not in ".:;!?,)":
            partes[-1] = partes[-1] + ", " + linea
        else:
            partes.append(linea)
    t = " ".join(partes)
    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,.;:!?])\1{1,}", r"\1", t)
    t = re.sub(r",\s*,+", ", ", t)
    t = re.sub(r"\(\s*\)", " ", t)              # paréntesis que quedan vacíos
    t = re.sub(r"([:;])\s*,\s*", r"\1 ", t)     # "Ver:, hecho" -> "Ver: hecho"
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" ,")


_VOCES_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/{ruta}")


def _descargar_voz(modelo):
    """Si falta la voz de piper, la DESCARGA (una vez) y devuelve la ruta buena.

    Las voces .onnx + .onnx.json pesan ~60 MB y NO vienen en el repo (ni en la
    imagen Docker): sin esto, un usuario recién instalado manda una nota de voz
    y el TTS falla con "model not found" (bug detectado probando la imagen).
    Se guardan junto al .env (agent/audio_models), que sobrevive a los rebuilds.
    Si la descarga falla, se devuelve el original y el fallo salta como siempre.
    """
    if not modelo or os.path.exists(modelo):
        return modelo
    try:
        import urllib.request
        base = os.path.basename(modelo)
        # es_ES-davefx-medium -> es/es_ES/davefx/medium/es_ES-davefx-medium
        partes = base[:-5].split("-") if base.endswith(".onnx") else []
        if len(partes) != 3:
            return modelo
        idioma, voz, calidad = partes
        # es_ES-davefx-medium  ->  es/es_ES/davefx/medium/es_ES-davefx-medium.onnx
        # en_US-lessac-medium  ->  en/en_US/lessac/medium/en_US-lessac-medium.onnx
        cod = partes[0].split("_")[0]
        ruta = f"{cod}/{partes[0]}/{voz}/{calidad}/{base}"
        os.makedirs(os.path.dirname(modelo) or ".", exist_ok=True)
        print(f"[audio] voz '{base}' no está → descargando (~60 MB, solo esta vez)…",
              flush=True)
        for sufijo, destino in (("", modelo), (".json", modelo + ".json")):
            if os.path.exists(destino):
                continue
            url = _VOCES_URL.format(ruta=ruta + sufijo)
            with urllib.request.urlopen(url, timeout=180) as r, open(destino, "wb") as fh:
                while True:
                    trozo = r.read(65536)
                    if not trozo:
                        break
                    fh.write(trozo)
        print("[audio] ✅ voz lista.", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[audio] ⚠️ no pude descargar la voz ({str(e)[:80]}); TTS sin voz natural.",
              flush=True)
    return modelo


def tts(text, dst, modelo=None):
    """TTS local con voz natural (piper). Genera un .wav.

    `modelo` permite elegir la VOZ (por defecto la española: así el traductor
    puede responder con voz inglesa). El texto se limpia con `humanizar()` para
    que NO lea asteriscos, emojis, URLs ni símbolos. Si la voz no está en disco,
    `_descargar_voz()` la trae sola antes de sintetizar.
    """
    limpio = humanizar(text) or (text or "")
    voz = _descargar_voz(modelo or PIPER_MODEL)
    _run([PIPER_BIN, "--model", voz, "--output_file", dst],
         inp=limpio.encode("utf-8"))
