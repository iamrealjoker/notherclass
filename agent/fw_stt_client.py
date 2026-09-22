"""
fw_stt_client.py — cliente del STT RESIDENTE (fw_stt.py --servir).

Motor COMPARTIDO por la web (agent/core/audio.py) y Telegram (translator/stt.py):
envía el wav al proceso residente con el modelo ya precargado (ahorra la carga
del modelo en cada nota, ~4-5s).

Env: FW_SERVE_PORT / TW_FW_STT_PORT (por defecto 5077).
Devuelve el texto, o None si el residente no está disponible (el llamador
cae entonces a su vía antigua por subprocess).
"""
import os
import urllib.request


def _puerto():
    return int(os.environ.get("FW_SERVE_PORT",
                              os.environ.get("TW_FW_STT_PORT", "5077")))


def residente_disponible():
    """True si el servidor residente está vivo en el puerto configurado."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{_puerto()}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def residente_transcribir(wav_path, timeout=30, lang=None):
    """POST del wav al residente. Devuelve texto o None si no está disponible.

    `lang` (opcional): 'es', 'en'... o 'auto'. Se manda por query string para
    que el residente transcriba en ESE idioma SIN recargar el modelo. Si es
    None no se manda nada y usa el idioma con el que arrancó el residente.
    """
    try:
        with open(wav_path, "rb") as f:
            data = f.read()
        ruta = f"http://127.0.0.1:{_puerto()}/transcribir"
        if lang:
            from urllib.parse import quote
            ruta += "?lang=" + quote(str(lang))
        req = urllib.request.Request(
            ruta, data=data,
            headers={"Content-Type": "application/octet-stream"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            return r.read().decode("utf-8", "replace").strip() or None
    except Exception:
        return None
