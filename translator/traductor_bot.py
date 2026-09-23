# -*- coding: utf-8 -*-
"""
Lengua. Traductor bidireccional espanol <-> ingles para Telegram.
Entrada: texto o nota de voz.
Salida: SIEMPRE la traduccion limpia + audio en el idioma de salida.
- Si el usuario escribe en espanol -> contesta en ingles (texto + voz en ingles).
- Si el usuario escribe en ingles  -> contesta en espanol (texto + voz en espanol).
Ademas, ante un mensaje de voz, responde con un texto breve indicando
lo que escucho y su traduccion, y luego el audio en el idioma contrario.
"""
import json, os, re, sys, time, threading

HERE = os.path.dirname(os.path.abspath(__file__))
# Ruta del agente CALCULADA (../agent respecto a translator/): así funciona
# clonando el repo donde quieras, no solo en la ruta de Docker.
AGENT = os.environ.get("TW_AGENT_DIR") or os.path.abspath(os.path.join(HERE, "..", "agent"))
for p in (AGENT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
from core import llm as L
from core import audio as A
from channels.telegram import TelegramBot
L._load_dotenv(os.path.join(AGENT, ".env"))
import stt as ST
import voz as VZ

TRAD_TOKEN = os.environ.get("TRAD_BOT_TOKEN", "").strip() or "TOKEN_DE_TELEGRAM_AQUI"
STATE = os.path.join(HERE, "STATE", "prefs.json")
os.makedirs(os.path.dirname(STATE), exist_ok=True)

# ---------------------------------------------------------------------------
# Deteccion de idioma por palabras funcionales (robusta, sin LLM)
# ---------------------------------------------------------------------------
ES_MARCA = set("hola que tal como estas bien mal gracias si no y o pero porque cuando donde quien cual es el la los las un una unos unas esto eso aquello mi tu su me te se nos os le les lo la muy mas menos con sin para por de del al".split())
EN_MARCA = set("hello hi how are you fine thanks yes no and or but because when where who which is the a an this that my your me we they he she it to for of from with without more less very much".split())


def _es_espanol(texto):
    """True si el texto parece espanol por mayoria de palabras funcionales."""
    t = (texto or "").lower()
    # quitar signos de puntuacion para no contaminar
    t = re.sub(r"[^a-záéíóúüñ\s]", " ", t)
    # separar por espacios y quedarse con tokens de 1+ letras sin acentos raros
    palabras = [p for p in re.split(r"\s+", t.strip()) if p]
    if not palabras:
        # sin palabras: mirar vocales tipicas
        return False
    # normalizar: quitar acentos para comparar contra EN_MARCA
    def norm(x):
        for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ü", "u"), ("ñ", "n")]:
            x = x.replace(a, b)
        return x
    score_es = sum(1 for p in palabras if norm(p) in ES_MARCA or any(c in p for c in "áéíóúñ"))
    score_en = sum(1 for p in palabras if norm(p) in EN_MARCA)
    # Ante empate (0 vs 0 u otros) preferimos NO asumir español: si no hay señal
    # clara de español (palabra funcional o tilde/ñ), tratamos como inglés.
    # Evita que una frase en inglés corta/sin marcas se "traduzca a inglés" (eco).
    return score_es > score_en


def _idioma_audio(es_origen):
    """Idioma en que debe generarse el audio de SALIDA."""
    return "es" if not es_origen else "en"

# ---------------------------------------------------------------------------
# Prompts con direccion EXPLICITA (no deja que el modelo decida)
# ---------------------------------------------------------------------------
def _prompt_direccion(es_origen):
    """Construye el system prompt segun la direccion de traduccion."""
    if es_origen:
        return (
            "Eres un traductor automatico de ESPANOL a INGLES.\n"
            "El usuario te escribe en espanol. Debes devolver SOLO la traduccion "
            "al ingles, sin prefijos, sin comillas, sin explicaciones, sin "
            "comentar el contenido ni el idioma.\n"
            "Nunca razones en voz alta. Nunca preguntes. Nunca anadas nada mas.\n\n"
            "Ejemplos:\n"
            "Usuario: hola\n"
            "Traduccion: hello\n\n"
            "Usuario: Como estas hoy?\n"
            "Traduccion: How are you today?\n\n"
            "Usuario: El clima esta frio\n"
            "Traduccion: The weather is cold\n\n"
            "Ahora traduce el mensaje del usuario."
        )
    else:
        return (
            "Eres un traductor automatico de INGLES a ESPANOL.\n"
            "El usuario te escribe en ingles. Debes devolver SOLO la traduccion "
            "al espanol, sin prefijos, sin comillas, sin explicaciones, sin "
            "comentar el contenido ni el idioma.\n"
            "Nunca razones en voz alta. Nunca preguntes. Nunca anadas nada mas.\n\n"
            "Ejemplos:\n"
            "Usuario: hello\n"
            "Traduccion: hola\n\n"
            "Usuario: How are you today?\n"
            "Traduccion: Como estas hoy?\n\n"
            "Usuario: I love this project\n"
            "Traduccion: Amo este proyecto\n\n"
            "Ahora traduce el mensaje del usuario."
        )


def _limpiar(t):
    """Quita prefijos, comillas y ruido tipico; deja UNA linea limpia."""
    if not t:
        return None
    t = re.sub(r"^(traducci[oO]n|translation|traducido al [a-z]+|en [a-z]+|[a-z]+->[a-z]+)\s*[:.\-]?\s*",
               "", t.strip(), flags=re.I)
    t = t.strip("\"'“”´`")
    # quitar prefijos tipo "Usuario:" o "Traduccion:" si aparecen en medio
    t = re.sub(r"^(usuario|traduccion|translation)\s*:\s*", "", t, flags=re.I)
    # quedarse con la primera linea no vacia (el modelo a veces razona y luego traduce)
    for linea in t.splitlines():
        linea = linea.strip()
        if linea:
            return linea
    return None


def traducir(texto):
    """Traduce texto. Devuelve (traduccion, era_espanol) o (None, None)."""
    if not texto or not texto.strip():
        return None, None
    texto = texto.strip()
    era_es = _es_espanol(texto)
    sys_p = _prompt_direccion(era_es)
    try:
        out, _ = L.chat_verbose(
            [{"role": "system", "content": sys_p},
             {"role": "user", "content": texto}],
            temperature=0.0,
            thinking='0',
            max_tokens=400
        )
    except Exception as e:
        print("Error traduciendo:", e)
        return None, None
    tr = _limpiar(out)
    return (tr, era_es) if tr else (None, None)

# ---------------------------------------------------------------------------
# Texto entrante
# ---------------------------------------------------------------------------
def on_texto(chat, texto):
    low = (texto or "").strip()
    if not low:
        return  # silencio total
    print(f"[Lengua] texto={low[:80]!r}")
    tr, era_es = traducir(low)
    if not tr:
        bot.send(chat, "No entendi.")
        return
    print(f"[Lengua] {low[:60]!r} dir={'ES->EN' if era_es else 'EN->ES'} -> {tr[:80]!r}")
    # Contestar SIEMPRE en el idioma contrario (texto)
    bot.send(chat, tr)
    # Y ademas audio en ese mismo idioma de salida
    idioma = _idioma_audio(era_es)
    ogg_out = _audio_prep(tr, idioma)
    if ogg_out:
        bot.send_voice(chat, ogg_out)

# ---------------------------------------------------------------------------
# Preparar audio de salida (texto -> ogg)
# ---------------------------------------------------------------------------
def _audio_prep(texto, idioma):
    wav = os.path.join(HERE, f".v{int(time.time())}.wav")
    ogg = wav[:-4] + ".ogg"
    ok = VZ.texto_a_audio(texto, wav, idioma)
    if not ok:
        return None
    VZ.wav_a_ogg(wav, ogg)
    return ogg if os.path.exists(ogg) else None


def on_voz(chat, file_id):
    """Telegram nos da un file_id (no una ruta). Descargamos el .oga antes."""
    try:
        ogg = os.path.join(HERE, f".v{int(time.time())}.ogg")
        remote = bot.get_file_path(file_id)
        bot.download_file(remote, ogg)
        if not os.path.exists(ogg) or os.path.getsize(ogg) == 0:
            bot.send(chat, "No pude leer el audio.")
            return
        wav = ogg[:-4] + ".wav"
        A.oga_to_wav(ogg, wav)
        if not os.path.exists(wav) or os.path.getsize(wav) == 0:
            bot.send(chat, "No pude leer el audio.")
            return
        transc = ST.transcribir(wav)
        if not transc:
            bot.send(chat, "No escuche nada claro.")
            return
        transc = transc.strip()
        print(f"[Lengua] voz transc={transc[:80]!r}")
        tr, era_es = traducir(transc)
        if not tr:
            bot.send(chat, "No pude traducir el audio.")
            return
        print(f"[Lengua] voz dir={'ES->EN' if era_es else 'EN->ES'} -> {tr[:80]!r}")
        # Decir lo que escuchaste y la traduccion
        # (el usuario quiere ver el texto recibido y la respuesta en el otro idioma)
        if era_es:
            bot.send(chat, f"Escuche (es): {transc}\nTraduccion (en): {tr}")
        else:
            bot.send(chat, f"Escuche (en): {transc}\nTraduccion (es): {tr}")
        # Y el audio SIEMPRE en el idioma de salida
        idioma = _idioma_audio(era_es)
        ogg_out = _audio_prep(tr, idioma)
        if ogg_out:
            bot.send_voice(chat, ogg_out)
        for f in (ogg, wav, ogg_out):
            try:
                if f and os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
    except Exception as e:
        print("Error voz:", e)
        bot.send(chat, "Error procesando el audio.")

# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
def main():
    global bot
    bot = TelegramBot(TRAD_TOKEN, on_message=on_texto, on_voice=on_voz)
    print("Lengua escuchando...")
    bot.run_forever()

if __name__ == "__main__":
    main()
