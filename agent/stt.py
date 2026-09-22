# -*- coding: utf-8 -*-
import os
import subprocess
import tempfile

AGENT = "/app/notherclass/agent"
FW_PY = os.path.join(AGENT, "venv311", "bin", "python")
FW_SCRIPT = os.path.join(AGENT, "fw_stt.py")
WHISPER_CLI = "/opt/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL = "/opt/whisper.cpp/models/ggml-base.bin"


def _residente(wav_path):
    """STT residente (fw_stt.py --servir): HTTP en 127.0.0.1:5077, modelo precargado.

    MISMA política/cliente que agent/core/audio.py y translator/stt.py (un solo
    cliente para todo). ANTES esta función usaba un socket UNIX /tmp/fw_stt.sock
    que el servidor NUNCA abre: fallaba en silencio y se caía al CLI lento.
    """
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
        if r.status == 200 and txt and not txt.startswith("error"):
            return txt
    except Exception:  # noqa: BLE001
        pass
    return ""


_AVISO = {}


def _aviso_lento():
    """⚠️ Deja RASTRO si caemos al modo LENTO (recarga el modelo por nota)."""
    import time
    if time.time() - _AVISO.get("ts", 0) < 60:
        return
    _AVISO["ts"] = time.time()
    print("[stt] ⚠️ residente STT NO disponible → FALLBACK lento (recarga el modelo "
          "en cada nota). Revisa 'reiniciar_agentes.py --estado'.", flush=True)


def transcribir(oga_path, idioma="auto"):
    txt = ""
    wav = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(["ffmpeg", "-y", "-i", oga_path, "-ar", "16000", "-ac", "1",
                        "-c:a", "pcm_s16le", wav], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env = dict(os.environ)
        if idioma and idioma != "auto":
            env["FW_LANG"] = idioma
        else:
            env.pop("FW_LANG", None)
        txt = _residente(wav)
        if not txt:
            _aviso_lento()
        if not txt and os.path.exists(FW_PY) and os.path.exists(FW_SCRIPT):
            try:
                r = subprocess.run([FW_PY, FW_SCRIPT, wav], env=env, capture_output=True)
                txt = (r.stdout or b"").decode("utf-8", "replace").strip()
            except Exception:
                txt = ""
        if not txt and os.path.exists(WHISPER_CLI):
            base = wav[:-4]
            subprocess.run([WHISPER_CLI, "-m", WHISPER_MODEL, "-f", wav,
                            "-l", (idioma if idioma != "auto" else "auto"),
                            "-t", "8", "-otxt", "-of", base], capture_output=True)
            p = base + ".txt"
            if os.path.exists(p):
                txt = open(p, encoding="utf-8").read().strip()
    finally:
        for p in (wav, wav[:-4] + ".txt"):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
    return txt
