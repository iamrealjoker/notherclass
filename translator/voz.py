# -*- coding: utf-8 -*-
"""Generador de voz local (piper) para el bot-traductor.

Mismo mÃ©todo que usa joker (core/audio.tts): llama al binario /opt/piper/piper
con LD_LIBRARY_PATH=/opt/piper. AquÃ­ mantenemos un dict de voces (es/en) y un
TTS sin dependencia de la persona de joker.
"""
import os
import subprocess

AGENT = "/app/notherclass/agent"
PIPER_BIN = "/opt/piper/piper"
ES_MODEL = os.path.join(AGENT, "audio_models", "es_ES-davefx-medium.onnx")
EN_MODEL = os.path.join(AGENT, "audio_models", "en_US-lessac-medium.onnx")

# idioma config de piper por defecto y su modelo
VOZ = {
    "es": ES_MODEL,
    "en": EN_MODEL,
}


def _correr(cmd, inp=None):
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = "/opt/piper:" + env.get("LD_LIBRARY_PATH", "")
    env["PIPER_DATA_DIR"] = "/opt/piper"
    return subprocess.run(cmd, input=inp, capture_output=True, env=env)


def texto_a_audio(texto, ruta_wav, idioma="es"):
    """Genera wav 16 bits mono 22050 (piper). Devuelve True si salio bien."""
    modelo = VOZ.get(idioma) or VOZ["es"]
    if not os.path.exists(modelo):
        return False
    r = _correr([PIPER_BIN, "--model", modelo, "--output_file", ruta_wav],
                inp=(texto or "").encode("utf-8"))
    return os.path.exists(ruta_wav) and os.path.getsize(ruta_wav) > 0


def wav_a_ogg(wav, ogg):
    subprocess.run(["ffmpeg", "-y", "-i", wav, "-c:a", "libopus", "-b:a", "20000", ogg],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return os.path.exists(ogg) and os.path.getsize(ogg) > 0


# smoke test si se ejecuta en solitario
if __name__ == "__main__":
    p = "/tmp/_voz_test.wav"
    ok = texto_a_audio("Hola, esto es una prueba de la voz en espanol.", p, "es")
    print("ES:", ok, ("(tam %d)" % os.path.getsize(p)) if os.path.exists(p) else "sin archivo")
    if ok:
        os.remove(p)
    p2 = "/tmp/_voz_test_en.wav"
    ok2 = texto_a_audio("Hello, this is a test of the English voice.", p2, "en")
    print("EN:", ok2, ("(tam %d)" % os.path.getsize(p2)) if os.path.exists(p2) else "sin archivo")
    if os.path.exists(p2):
        os.remove(p2)
