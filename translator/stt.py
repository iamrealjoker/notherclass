# -*- coding: utf-8 -*-
"""STT para el bot traductor — usa faster-whisper (el mismo motor rápido que el agente Joker).
Recibe un .wav ya convertido por core/audio.oga_to_wav y devuelve el texto.
Fallback a whisper.cpp si faster-whisper no está disponible.
"""
import os
import subprocess

AGENT = "/app/notherclass/agent"
FW_PY = os.path.join(AGENT, "venv311", "bin", "python")
FW_SCRIPT = os.path.join(AGENT, "fw_stt.py")
WHISPER_CLI = "/opt/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL = "/opt/whisper.cpp/models/ggml-base.bin"
# Política ÚNICA con la web: modelo base + idioma forzado (calidad primero).
# Vía preferente: motor RESIDENTE (fw_stt.py --servir, modelo ya precargado).
FW_SIZE = os.environ.get("TW_FW_SIZE", "base")
FW_THREADS = os.environ.get("TW_FW_THREADS", "8")


def transcribir(wav_path, idioma="auto"):
    """Transcribe un .wav (16kHz mono) a texto. Rápido: faster-whisper."""
    if not os.path.exists(wav_path):
        return ""
    # 0) STT RESIDENTE (misma política que la web: base, sin recargar modelo).
    #    Si piden idioma concreto pasamos al CLI (el residente fija su FW_LANG al arrancar).
    if not (idioma and idioma != "auto"):
        try:
            import fw_stt_client as _cli
            if _cli.residente_disponible():
                txt = _cli.residente_transcribir(wav_path)
                if txt:
                    return txt
        except Exception:
            pass
    # 1) faster-whisper CLI (rápido, sin reconversión)
    if os.path.exists(FW_PY) and os.path.exists(FW_SCRIPT):
        env = dict(os.environ)
        if idioma and idioma != "auto":
            env["FW_LANG"] = idioma
        else:
            env.pop("FW_LANG", None)
        env["FW_SIZE"] = FW_SIZE
        env["FW_THREADS"] = FW_THREADS
        try:
            r = subprocess.run([FW_PY, FW_SCRIPT, wav_path], env=env,
                               capture_output=True, timeout=60)
            txt = (r.stdout or b"").decode("utf-8", "replace").strip()
            if txt:
                return txt
        except Exception:
            pass
    # 2) fallback whisper.cpp
    if os.path.exists(WHISPER_CLI):
        try:
            base = wav_path[:-4]
            subprocess.run([WHISPER_CLI, "-m", WHISPER_MODEL, "-f", wav_path,
                            "-l", "auto", "-t", "8", "-otxt", "-of", base],
                           capture_output=True, timeout=60)
            p = base + ".txt"
            if os.path.exists(p):
                txt = open(p, encoding="utf-8").read().strip()
                if txt:
                    return txt
        except Exception:
            return ""
    return ""


def audio_a_texto(oga_path, idioma="auto"):
    """Compatibilidad: recibe .ogg/.oga, lo convierte a wav y transcribe."""
    import tempfile
    wav = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(["ffmpeg", "-y", "-i", oga_path, "-ar", "16000", "-ac", "1",
                        "-c:a", "pcm_s16le", wav],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return transcribir(wav, idioma)
    finally:
        if os.path.exists(wav):
            os.remove(wav)
