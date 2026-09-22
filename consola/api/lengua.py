# -*- coding: utf-8 -*-
"""lengua.py — Traductor ES<->EN con la MISMA lógica que el bot de Telegram
("Lengua", translator/traductor_bot.py), para que la WEB se comporte igual:
mandas español -> contesta en inglés (texto + voz inglesa) y al revés.

No se puede importar `traductor_bot.py` directamente porque al importarse
arranca un bot de Telegram; por eso aquí viven las partes PURAS (detección de
idioma por palabras funcionales, prompts con dirección explícita y limpieza de
la respuesta). El cliente LLM se INYECTA (`llamar`), así sirve tanto para la
consola como para cualquier otro consumidor.
"""
import re

# Detección de idioma por palabras funcionales (robusta, sin LLM) — igual que
# en el bot de Telegram.
ES_MARCA = set("hola que tal como estas bien mal gracias si no y o pero porque "
               "cuando donde quien cual es el la los las un una unos unas esto eso "
               "aquello mi tu su me te se nos os le les lo la muy mas menos con sin "
               "para por de del al".split())
EN_MARCA = set("hello hi how are you fine thanks yes no and or but because when "
               "where who which is the a an this that my your me we they he she it "
               "to for of from with without more less very much".split())


def es_espanol(texto):
    """True si el texto parece español (mayoría de palabras funcionales)."""
    t = re.sub(r"[^a-záéíóúüñ\s]", " ", (texto or "").lower())
    palabras = [p for p in re.split(r"\s+", t.strip()) if p]
    if not palabras:
        return False
    es = sum(1 for p in palabras if p in ES_MARCA)
    en = sum(1 for p in palabras if p in EN_MARCA)
    return es >= en


def idioma_salida(era_espanol):
    """Idioma del TEXTO/AUDIO de salida (si entró español -> sale inglés)."""
    return "en" if era_espanol else "es"


def prompt_direccion(es_origen):
    """System prompt con la dirección EXPLÍCITA (no deja decidir al modelo)."""
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


def limpiar(t):
    """Quita prefijos, comillas y ruido; deja UNA línea limpia (la traducción)."""
    if not t:
        return None
    t = re.sub(r"^(traducci[oO]n|translation|traducido al [a-z]+|en [a-z]+|[a-z]+->[a-z]+)\s*[:.\-]?\s*",
               "", t.strip(), flags=re.I)
    t = t.strip("\"'“”´`")
    t = re.sub(r"^(usuario|traduccion|translation)\s*:\s*", "", t, flags=re.I)
    for linea in t.splitlines():
        linea = linea.strip()
        if linea:
            return linea
    return None


def traducir(texto, llamar):
    """Traduce a la inversa del idioma detectado.

    `llamar(mensajes) -> str` lo inyecta quien use el módulo (la consola le pasa
    su propio cliente LLM). Devuelve (traduccion, era_espanol) o (None, None).
    """
    if not texto or not texto.strip():
        return None, None
    texto = texto.strip()
    era_es = es_espanol(texto)
    try:
        out = llamar([{"role": "system", "content": prompt_direccion(era_es)},
                      {"role": "user", "content": texto}])
    except Exception:
        return None, None
    tr = limpiar(out)
    return (tr, era_es) if tr else (None, None)
