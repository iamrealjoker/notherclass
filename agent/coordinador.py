"""
coordinador.py — AUTO-TRIGGER del diálogo bot<->bot en el GRUPO (JokerV3).

PROBLEMA
--------
Telegram NO entrega a un bot los mensajes de OTRO bot. Así que cuando JokerV1
escribe en el grupo/pizarra "@jokerv2 ...", JokerV2 NO se despierta solo. La
charla no se auto-sostiene: hoy cada turno necesita que el humano @mencione a
cada bot. Este coordinador cierra ese hueco.

QUÉ HACE
--------
Vigila la PIZARRA compartida (grupo_bus/chat_<id>.jsonl). Cuando aparece una
línea nueva que menciona a un bot (venida del humano o del OTRO bot), monta el
prompt con el contexto de la pizarra y DESPIERTA a ese bot ejecutando UN turno
real (agent/turno_grupo.py). La respuesta del bot se envía al grupo y se apunta
en la pizarra -> el siguiente ciclo despierta al otro bot. Y así la conversación
fluye sola, con un TOPE de turnos por ronda.

PROTOCOLO EN 2 FASES (lo que pidió el humano)
---------------------------------------------
  FASE 1 (conversación): el humano @menciona a un bot -> los dos bots hablan por
    la pizarra, acuerdan un plan y, en el último turno, le PIDEN CONFIRMACIÓN.
    Tope por defecto: 4 turnos.
  CONFIRMACIÓN: la interpreta liljoker (LLM) por INTENCIÓN, no por palabras fijas:
    entiende "hazlo", "ok, aplícalo", "adelante", "dale"... en cualquier idioma.
  FASE 2 (ejecución): los bots se REPARTEN el trabajo ("yo hago X, tú haces Y"),
    explican cada paso y usan un CANDADO para no tocar el mismo archivo.
    Tope por defecto: 6 turnos.

SEGURIDAD
---------
  · turno_grupo.py fuerza TW_CONFIRM_EDITS=1: toda ESCRITURA real queda en pausa
    esperando el "sí/no" del humano. El coordinador NO ejecuta cambios por su
    cuenta; reparte y propone.
  · Un único coordinador a la vez (lock con flock).
  · --dry-run y --simular para probar SIN gastar LLM ni mandar mensajes.

USO
---
  python3 coordinador.py                 # vigila la pizarra y coordina
  python3 coordinador.py --dry-run       # no ejecuta nada (solo muestra)
  python3 coordinador.py --simular "@jokerv2 hola, explica a jokerv1 la idea"
  python3 coordinador.py --max-conv 4 --max-exec 6
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUS_DIR = os.path.join(ROOT, "grupo_bus")
CONFIG = os.path.join(ROOT, "configAgentes.json")
STATE_F = os.path.join(BUS_DIR, "coordinador_state.json")
LOCK_F = os.path.join(BUS_DIR, "coordinador.lock")

# Credenciales/modelo para las llamadas LLM de liljoker: cargamos el .env del
# agente para que el coordinador funcione aunque se lance fuera del daemon.
try:
    sys.path.insert(0, HERE)
    from core import llm as _llm_boot  # noqa: E402
    _llm_boot._load_dotenv(os.path.join(HERE, ".env"))
except Exception:  # noqa: BLE001
    pass

# NOTA: aquí vivían los diccionarios de palabras fijas (CONFIRMA_KW, NUEVA_RONDA_KW,
# CONVERSAR_KW). SE HAN ELIMINADO: ahora **liljoker es el verdadero coordinador** y
# decide la intención, la invocación, el consenso y el bucle con la MISMA llamada
# unificada (`_decide`), sin listas de palabras.


# ── Coste de liljoker: SIEMPRE visible ─────────────────────────────────────
# Todas las llamadas a liljoker pasan por aquí, se contabilizan y se imprimen,
# para que NUNCA se pierda de vista lo que cuesta.
_LILJOKER = {"calls": 0, "in": 0, "out": 0, "cost": 0.0}



def _glm_key():
    """Clave de GLM desde configAgentes.json (api_keys.zai_primary)."""
    try:
        with open(CONFIG, "r", encoding="utf-8") as fh:
            return (json.load(fh).get("api_keys") or {}).get("zai_primary", "")
    except Exception:  # noqa: BLE001
        return ""


def _llm_liljoker(msgs, max_tokens=200):
    """Llama al modelo PARA liljoker (GLM por defecto) y registra/imprime su coste.

    liljoker usa **GLM** (z.ai) por decisión del humano. Se puede cambiar con:
    TW_LILJOKER_PROVIDER / TW_LILJOKER_MODEL / TW_LILJOKER_API_KEY / TW_LILJOKER_BASE_URL."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from core import llm as _llm  # noqa: E402
    prov = os.environ.get("TW_LILJOKER_PROVIDER", "glm")
    mdl = os.environ.get("TW_LILJOKER_MODEL", "glm-5.3-flash")
    key = os.environ.get("TW_LILJOKER_API_KEY") or _glm_key()
    base = (os.environ.get("TW_LILJOKER_BASE_URL")
            or "https://api.z.ai/api/paas/v4/chat/completions")
    out, meta = _llm.chat_verbose(msgs, temperature=0.0, max_tokens=max_tokens,
                                  provider=prov, model=mdl, api_key=key,
                                  base_url=base, price_provider=prov,
                                  thinking=("1" if prov in ("glm", "zai") else None),
                                  reasoning_effort=("high" if prov in ("glm", "zai") else None))
    try:
        u = meta.get("usage", {}) or {}
        _LILJOKER["calls"] += 1
        _LILJOKER["in"] += u.get("prompt_tokens", 0)
        _LILJOKER["out"] += u.get("completion_tokens", 0)
        _LILJOKER["cost"] += meta.get("cost", 0.0)
        print(f"[liljoker] 💰 ${meta.get('cost', 0):.6f} · in={u.get('prompt_tokens', 0)} "
              f"out={u.get('completion_tokens', 0)} | Σ ${_LILJOKER['cost']:.5f} "
              f"({_LILJOKER['calls']} llamadas)", flush=True)
    except Exception:  # noqa: BLE001
        pass
    return out, meta


def _liljoker_coste_txt():
    """Texto del gasto acumulado de liljoker (para incluirlo en los avisos)."""
    return (f"liljoker: ${_LILJOKER['cost']:.5f} · {_LILJOKER['calls']} llamadas "
            f"(in={_LILJOKER['in']:,}, out={_LILJOKER['out']:,})")


# ─────────── UNA sola llamada: liljoker decide TODO sobre un mensaje ─────────
DECIDE_SYS = (
    "Eres liljoker, el COORDINADOR de un grupo de agentes que comparten una pizarra. "
    "Recibes UN mensaje, quién lo escribió (HUMANO o AGENTE), los COMPAÑEROS disponibles "
    "y el ESTADO de la ronda. Analiza y devuelve SOLO un JSON:\n"
    '{"accion": "...", "objetivo": "...", "consenso": true|false, "bucle": true|false, '
    '"avanza": true|false, "paralelo": true|false, "desacuerdo": true|false, '
    '"reparto": "...", "mandato": true|false, "dime": "...", "resumen": "..."}\n'
    "· accion (SOLO si escribe el HUMANO): "
    "'conversar' = pide PLANEAR/organizar/debatir/REPARTIRSE el trabajo o hablar entre "
    "ellos ('hablad entre vosotros', 'dividíoslo', 'organizaos', '¿cómo lo hacéis?', "
    "aunque diga 'para hacerlo rápido'): primero se propone y se ACUERDA, NO se ejecuta. "
    "'parar' = pide DETENERSE/ESPERAR o PEDIR UN RESUMEN/informe ('parad', 'esperad un "
    "poco', 'dejadme analizarlo', 'dadme un resumen', '¿qué habéis hecho?', 'pausa'). "
    "'ejecutar' = AUTORIZA el plan YA acordado ('vale/adelante/hazlo/aplícalo/confirmo'), "
    "o ARBITRA ('@X tiene razón', 'sigue lo que dice @X', 'hazlo como dice @X'), o pide "
    "un cambio concreto y directo. "
    "'nueva_ronda' = nuevo tema/tarea; 'ninguna' = charla o pregunta suelta.\n"
    "· objetivo: si el mensaje PIDE/INVOCA a un compañero (aunque no use '@'), su "
    "username EXACTO de la lista; si no, \"\".\n"
    "· consenso: true si los agentes YA han acordado o dan el asunto por cerrado sin "
    "aportar nada nuevo.\n"
    "· bucle: true si REPITEN lo mismo sin avanzar (mismas conclusiones/verificaciones).\n"
    "· avanza: true si el mensaje APORTA algo nuevo (propone, corrige, ejecuta o "
    "verifica algo NUEVO); false si solo se despide o repite.\n"
    "· paralelo: true SOLO si AHORA van a ESCRIBIR/EDITAR archivos y pueden hacerlo "
    "A LA VEZ sin pisarse (cada uno su parte/archivo: fase de EJECUCIÓN). false si "
    "están CONVERSANDO/organizándose o comparten los mismos archivos (entonces van "
    "de uno en uno).\n"
    "· desacuerdo: true si los agentes NO se ponen de acuerdo (defienden caminos "
    "distintos/enfrentados y ninguno cede). Entonces decide el HUMANO: no sigas.\n"
    "· reparto: si se va a EJECUTAR, QUIÉN hace QUÉ en 1 línea corta y SIN solapes "
    "(p. ej. \"@a: app.js; @b: index.html\"). Vacío si aún no toca repartir.\n"
    "· dime: UNA frase CORTA en ESPAÑOL, en 1ª persona y dirigida al humano, "
    "explicando QUÉ vas a hacer ahora y POR QUÉ (natural y concreta; sin datos "
    "inventados — usa solo lo que sabes del mensaje y del estado).\n"
    "· mandato: true si el HUMANO pide que los agentes SE COORDINEN, HABLEN ENTRE "
    "ELLOS o LLEGUEN A UN ACUERDO ('habladlo entre vosotros', 'coordinaos', 'llegad a "
    "un acuerdo', 'hacedlo a la vez'). false si no pide coordinarse. NO resumas nada: "
    "el mandato será SU MENSAJE LITERAL.\n"
    "· resumen: 1 frase del estado real (máx 12 palabras).\n"
    "Responde SOLO el JSON, sin texto adicional, y BREVE (no te pases del límite)."
)


def _decide(texto, bots, autor, es_humano, estado=None, intentos=2):
    """UNA llamada a liljoker → TODO el análisis del mensaje (acción, a quién invoca,
    consenso, bucle, si avanza y un resumen). Reemplaza a las 3-4 llamadas anteriores.

    ROBUSTO: reintenta una vez, extrae el JSON de forma tolerante y SIEMPRE devuelve
    un dict con todas las claves (valores por defecto seguros si el LLM fallara)."""
    companeros = [u for u in (bots or {}) if u != (autor or "")]
    user = ("AUTOR: " + ("HUMANO" if es_humano else "@" + (autor or "?")) + "\n"
            + "COMPAÑEROS: " + (", ".join(companeros) or "(ninguno)") + "\n"
            + "ESTADO: " + json.dumps(estado or {}, ensure_ascii=False) + "\n"
            + "MENSAJE:\n" + (texto or "")[:900])
    d = {}
    for _ in range(max(1, intentos)):
        try:
            out, _m = _llm_liljoker([{"role": "system", "content": DECIDE_SYS},
                                     {"role": "user", "content": user}], max_tokens=700)
            i, j = out.find("{"), out.rfind("}")
            if i != -1 and j > i:
                cand = json.loads(out[i:j + 1])
                if isinstance(cand, dict) and cand:
                    d = cand
                    break
        except Exception:  # noqa: BLE001
            pass
    if not d:
        print("[liljoker] ⚠️ no devolvió JSON válido; uso valores por defecto.",
              flush=True)
    acc = str(d.get("accion", "ninguna")).strip().lower()
    if acc not in ("ejecutar", "conversar", "nueva_ronda", "ninguna", "parar"):
        acc = "ninguna"
    obj = str(d.get("objetivo", "")).strip().lstrip("@").lower()
    if obj not in companeros:
        obj = ""
    return {"accion": acc, "objetivo": obj,
            "consenso": bool(d.get("consenso")),
            "bucle": bool(d.get("bucle")),
            "avanza": bool(d.get("avanza", True)),
            "paralelo": bool(d.get("paralelo")),
            "desacuerdo": bool(d.get("desacuerdo")),
            "reparto": str(d.get("reparto", ""))[:300],
            "mandato": bool(d.get("mandato")),
            "dime": str(d.get("dime", ""))[:220],
            "resumen": str(d.get("resumen", ""))[:200]}


def _intencion_humano(txt):
    """Intención del humano (en cualquier idioma o frase) — la decide liljoker con la
    MISMA llamada unificada `_decide`. SIN diccionarios de palabras fijas."""
    d = _decide(txt, {}, "", True)
    return {"accion": d["accion"], "short": d["resumen"]}


# ──────────────────── liljoker #2: lee a los AGENTES y detecta consenso ───────
# El humano lo pidió: no gastar los 6 turnos a lo tonto. Si los agentes ya han
# llegado a un ACUERDO (han acordado el plan o quién hace qué, y seguir sería
# repetirse), liljoker CORTA la ronda y devuelve el control al humano.
CONSENSO_SYS = (
    "Eres liljoker, observador de un debate entre agentes de IA que comparten una "
    "pizarra. Recibes los ÚLTIMOS mensajes de los agentes, en orden (el último es el "
    "más reciente). Decide si ya han LLEGADO A UN ACUERDO REAL (consenso): están de "
    "acuerdo en el plan o en quién hace qué, o ambos dan el asunto por cerrado y no "
    "aportan nada nuevo (p. ej. 'de acuerdo', 'perfecto', 'quedamos así', 'listo para "
    "ejecutar', 'por mí ok'). NO es consenso si todavía proponen, corrigen, preguntan, "
    "piden al otro o se pasan el turno para seguir hablando. Responde SOLO una línea "
    "JSON: {\"consenso\": true|false, \"short\": \"motivo en 1 frase\"}."
)


def _intencion_consenso(lineas):
    """liljoker lee los mensajes recientes de los AGENTES y decide si hay consenso.
    `lineas` = lista de strings '· autor: texto'. Devuelve {"consenso": bool, "short": str}.
    Si el LLM falla, NO asume consenso (devuelve False) para no cortar de más."""
    txt = "\n".join(lineas)[-2500:]
    try:
        out, _meta = _llm_liljoker(
            [{"role": "system", "content": CONSENSO_SYS},
             {"role": "user", "content": "MENSAJES DE LOS AGENTES:\n" + txt}],
            max_tokens=160)
        i, j = out.find("{"), out.rfind("}")
        data = json.loads(out[i:j + 1]) if i != -1 and j > i else {}
        return {"consenso": bool(data.get("consenso")),
                "short": str(data.get("short", ""))[:160]}
    except Exception as e:  # noqa: BLE001
        return {"consenso": False, "short": f"sin LLM: {e}"}


def _hay_consenso(chat, bots, n=6):
    """liljoker #2 EN ACCIÓN: lee el intercambio de ESTA ronda en la pizarra y
    decide si los agentes YA han llegado a un acuerdo, para cortar la ronda sin
    gastar los turnos que quedan (el humano: 'llegan a consenso en el 4 y siguen
    hasta el 6'). Devuelve {"consenso": bool, "short": str}.

    Solo mira las líneas POSTERIORES al último mensaje del humano, para no
    confundirse con el consenso de una ronda anterior."""
    rows = _leer_board(chat)
    # inicio = justo tras el último mensaje que NO sea de un bot (el humano u otro)
    inicio = 0
    for i in range(len(rows) - 1, -1, -1):
        autor = (rows[i].get("autor") or "").lstrip("@").lower()
        if autor not in bots:
            inicio = i + 1
            break
    lineas = []
    for r in rows[inicio:]:
        autor = r.get("autor") or ""
        if autor.lstrip("@").lower() in bots:
            txt = (r.get("texto") or "").replace("\n", " ").strip()
            if len(txt) > 700:
                txt = txt[:700] + "…"
            lineas.append(f"· {autor}: {txt}")
    if len(lineas) < 2:
        return {"consenso": False, "short": "aún no hay intercambio suficiente"}
    return _intencion_consenso(lineas[-n:])


def _avisar(chat, bots, texto):
    """Manda un aviso de texto al GRUPO sin escribirlo en la pizarra (así NO
    re-dispara el bucle de turnos). Usa el token del primer bot disponible."""
    if not bots:
        return False
    bot = next(iter(bots.values()))
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        from channels.telegram import TelegramBot  # noqa: E402
        TelegramBot(bot["token"]).send(chat, texto, publicar=False)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[coordinador] ⚠️ no pude avisar al grupo: {e}", flush=True)
        return False


def _avisar_fallo(chat, bots, humano, objetivo, nfallos):
    """Aviso al humano cuando un bot no se deja despertar tras varios intentos.
    (Antes se llamaba a esta función sin estar definida -> NameError latente.)"""
    _avisar(chat, bots,
            f"⚠️ @{humano}: el coordinador no pudo despertar a {objetivo} tras "
            f"{nfallos} intentos. Abandono esta ronda; revisa el log del coordinador.")


# ─────────────────────────── utilidades de pizarra ───────────────────────────
def _board_file(chat):
    return os.path.join(BUS_DIR, f"chat_{int(chat)}.jsonl")


def _tsnum(x):
    """ts -> float tolerante. Acepta números o fechas ISO ('2026-09-12'). Si no
    se puede, devuelve 0.0 en vez de reventar el bucle."""
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        pass
    # fecha ISO 'YYYY-MM-DD' o 'YYYY-MM-DDTHH:MM:SS'
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _norm_linea(e):
    """Normaliza una entrada de la pizarra a {'ts','autor','texto','msg_id'}.
    Tolera esquemas alternativos ({'from','text'}) y descarta líneas vacías."""
    if not isinstance(e, dict):
        return None
    autor = e.get("autor") or e.get("from") or ""
    texto = e.get("texto") or e.get("text") or ""
    if not autor and not texto:
        return None
    return {"ts": _tsnum(e.get("ts")), "autor": autor, "texto": texto,
            "msg_id": e.get("msg_id") or e.get("message_id"),
            "para": e.get("para")}


def _leer_board(chat):
    p = _board_file(chat)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            n = _norm_linea(e)
            if n is not None:
                out.append(n)
    return out


def _detectar_chat():
    for p in sorted(glob.glob(os.path.join(BUS_DIR, "chat_*.jsonl"))):
        m = re.search(r"chat_(-?\d+)\.jsonl$", p)
        if m:
            return int(m.group(1))
    return None


# ─────────────────────────── identidad de los bots ───────────────────────────
def _bots(permitidos=None):
    """{username_lower: {'id': agent_id, 'name':..., 'token':..., 'username':...}}
    Resuelve el @username real con getMe (esta cacheado en el proceso).
    `permitidos` (set de ids): si se pasa, SOLO esos agentes cuentan. Es clave
    para no confundir el par jokerv1/jokerv2 con otros bots de la config que ni
    siquiera están en el grupo (joker principal, horas_extras, ...)."""
    bots = {}
    try:
        with open(CONFIG, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        return bots
    sys.path.insert(0, HERE)
    from channels.telegram import TelegramBot  # noqa: E402
    for aid, e in (cfg.get("agentes") or {}).items():
        if permitidos is not None and aid not in permitidos:
            continue
        tok = (e.get("telegram_bot_token") or "").strip()
        if not tok:
            continue
        try:
            tb = TelegramBot(tok)
            un = (tb.username or "").lower()
            if not un:
                continue
            bots[un] = {"id": aid, "name": e.get("name", aid), "token": tok,
                        "username": tb.username, "bot_id": tb.bot_id}
        except Exception:
            continue
    return bots


def _es_humano(autor, bots):
    a = (autor or "").lower().lstrip("@")
    return a not in bots


def _mencionados(texto, bots):
    """Lista de usernames de bot mencionados (@user) en el texto, en orden."""
    low = (texto or "").lower()
    return [u for u in bots if ("@" + u) in low]


# ─────────────────────────────── estado ──────────────────────────────────────
def _load_state():
    try:
        with open(STATE_F, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"cursor": 0.0, "turnos": 0, "fase": "conversacion",
                "ultimo_humano": ""}


def _save_state(st):
    os.makedirs(BUS_DIR, exist_ok=True)
    with open(STATE_F, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)


# ─────────────────────────────── prompt ──────────────────────────────────────
def _contexto(chat, mi_user, n=None):
    """DIGEST corto de la pizarra (no envía "muchísimo texto").

    Da las últimas N intervenciones de los DEMÁS (recortadas) + un PUNTERO al
    histórico completo para que el agente LEA el detalle con read_file/grep si lo
    necesita. NO se le quitan herramientas: solo se le da lo esencial y la vía
    para ampliar. Parámetros por entorno: TW_PIZARRA_N (def. 6), TW_PIZARRA_CHARS (def. 350).
    """
    p = _board_file(chat)
    if not os.path.exists(p):
        return ""
    if n is None:
        try:
            n = int(os.environ.get("TW_PIZARRA_N", "6"))
        except (TypeError, ValueError):
            n = 6
    try:
        tope = int(os.environ.get("TW_PIZARRA_CHARS", "350"))
    except (TypeError, ValueError):
        tope = 350
    todas = _leer_board(chat)
    items = todas[-(n * 2):]
    items = [e for e in items if (e.get("autor") or "").lstrip("@").lower()
             != mi_user.lstrip("@").lower()][-n:]
    lineas = []
    for e in items:
        txt = (e.get("texto") or "").replace("\n", " ").strip()
        if len(txt) > tope:
            txt = txt[:tope] + "…"
        lineas.append(f"· {e.get('autor')}: {txt}")
    if not lineas:
        return ""
    rel = os.path.relpath(p, ROOT)
    return (f"[PIZARRA — resumen: últimas {len(lineas)} intervenciones de los DEMÁS (no tú)]\n"
            + "\n".join(lineas)
            + f"\n[FIN RESUMEN] · Histórico COMPLETO ({len(todas)} líneas): {rel} "
              f"— léelo con read_file/grep SOLO si necesitas el detalle.\n\n")


def _parte_de(reparto, username):
    """Saca del reparto (texto libre decidido por lilJoker) la parte que le toca a
    `@username`, para mandársela a ESE bot concreto. '' si no se distingue."""
    if not reparto or not username:
        return ""
    for trozo in re.split(r"[;\n]|\s·\s|\s\|\s", str(reparto)):
        if ("@" + str(username)).lower() in trozo.lower():
            return trozo.strip(" -·:")
    return ""


def _build_prompt(bot, otro, humano, chat, fase, n, cap, final, paralelo=False,
                  reparto="", mandato="", informe=False):
    ctx = _contexto(chat, bot["username"])
    # La fase "reparto" usa las reglas de CONVERSACIÓN (todavía NO se escribe
    # nada), pero su objetivo es repartirse el trabajo: se añade `rep_txt` abajo.
    fase_reglas = "conversacion" if fase == "reparto" else fase
    if fase_reglas == "ejecucion":
        fase_txt = ("FASE 2 — EJECUCIÓN (el humano YA autorizó: 'hazlo/termina/sigue').")
        reglas = (
            f"- El humano AUTORIZÓ: **ACTÚA AHORA** con tus herramientas (las ESCRITURAS "
            "están permitidas). No pidas permiso otra vez.\n"
            "- PROHIBIDO repetir verificaciones ya hechas (mismo curl/grep de lo mismo) y "
            "volver a decir 'CERRADO'. Si YA estaba hecho: dilo en UNA línea y cierra; si "
            "falta algo, HAZLO ya y verifica SOLO lo nuevo.\n"
            "- Empieza tu mensaje con una línea `ESTADO: <resumen real en 1 frase>`.\n"
            f"- Reparte: di QUÉ harás TÚ y QUÉ hará {otro['name']}, archivo por archivo, "
            "sin solapar (CANDADO: un archivo solo lo toca uno).\n"
            "- SÉ BREVE (máx ~80 palabras), en español, solo lo esencial.\n"
            "- Termina SIEMPRE con dos líneas exactas: \"Hice: ...\" y \"Haré: ...\".\n"
        )
        cierre = (f"- Termina mencionando a @{otro['username']} para que continúe el turno."
                  if not final else
                  f"- ÚLTIMO turno ({n}/{cap}): FIN de la ronda de ejecución. NO menciones "
                  f"al otro bot. Resume en 2-3 líneas qué quedaría hecho y qué falta, y CIERRA "
                  f"avisando al humano (@{humano}) de que los turnos se AGOTARON y de cómo seguir, "
                  f"p. ej.: '@{humano} fin de ronda ({n}/{cap}) — di \"@{bot['username']} nueva "
                  f"ronda: <tema>\" para continuar o \"@{bot['username']} aplica\" para ejecutar'.")
    else:
        fase_txt = "FASE 1 — CONVERSACIÓN (resumid el estado, proponed el plan y decidid la vía)."
        reglas = (
            f"- Objetivo: RESUMIR + DECIDIR + PROPONER un plan corto junto con {otro['name']}.\n"
            "- Empieza tu mensaje con `ESTADO: <resumen real en 1 frase>` (qué hay ahora).\n"
            "- Si el humano os pide ORGANIZAROS o REPARTIR el trabajo: explica el problema, "
            "propón el reparto paso a paso (quién hace qué, archivo por archivo) y PREGUNTA "
            f"a @{otro['username']} qué le parece. NO ejecutes NADA hasta que el humano lo "
            "APRUEBE.\n"
            "- Luego propón el plan en 2-4 pasos y quién hace qué; critica con criterio si "
            "hace falta. NO repitas verificaciones ya hechas ni lo que dijo el otro.\n"
            "- NO ejecutes escrituras todavía (espera la autorización del humano).\n"
            "- SÉ BREVE (máx ~60 palabras) en español, solo lo esencial.\n"
            "- Termina SIEMPRE con dos líneas exactas: \"Hice: ...\" y \"Haré: ...\".\n"
        )
        cierre = (f"- Termina SIEMPRE mencionando a @{otro['username']} para que la pizarra "
                  "lo despierte y continúe." if not final else
                  f"- ÚLTIMO turno ({n}/{cap}): FIN de la ronda de conversación. NO menciones "
                  f"al otro bot. Resume en 2-3 líneas el plan acordado y CIERRA avisando al humano "
                  f"(@{humano}) de que los turnos se AGOTARON y de cómo seguir, p. ej.: "
                  f"'@{humano} fin de ronda ({n}/{cap}) — di \"@{bot['username']} confirmo\" para "
                  f"ejecutar, o \"@{bot['username']} nueva ronda: <tema>\" para seguir hablando'.")
    if fase == "reparto":
        fase_txt = ("FASE 1.5 — REPARTO: vais a EJECUTAR, pero PRIMERO os repartís el "
                    "trabajo para no solaparos ni pisaros. Todavía NO se escribe nada.")
        rep_txt = ("\n🧩 ESTE TURNO TOCA REPARTO: propón qué archivo/parte hace CADA uno "
                   "(@tú y @compañero), SIN solapes, y pídele que lo confirme o lo corrija.\n")
    elif fase == "ejecucion" and reparto:
        parte = _parte_de(reparto, bot["username"])
        rep_txt = ("\n📌 REPARTO YA ACORDADO — TU TAREA: " + (parte or reparto) +
                   "\n   NO toques la parte del otro.\n")
    else:
        rep_txt = ""
    # MANDATO del humano ("habladlo entre vosotros y llegad a un acuerdo"): se
    # REPITE en cada turno hasta que acuerden. Lleva SUS PALABRAS EXACTAS para que
    # el bot se lo PASE TAL CUAL al compañero (nada de resúmenes que pierdan detalle)
    # y no se limite a informar al humano.
    man_txt = ""
    if mandato and fase in ("conversacion", "reparto"):
        man_txt = (
            "\n🔴 MANDATO ABIERTO DEL HUMANO — estas son SUS PALABRAS EXACTAS:\n"
            f"\"{mandato}\"\n"
            f"- HABLA CON @{otro['username']} y PÁSALE ESE TEXTO TAL CUAL para que sepa "
            "exactamente qué ha pedido el humano (no lo resumas).\n"
            "- Di tu DECISIÓN/propuesta y pídele la suya; critica la suya con criterio.\n"
            "- El objetivo AHORA es llegar a un ACUERDO (no cerrar tú solo ni informar "
            "solo al humano).\n"
        )
    # RIGOR: reglas que atacan los fallos REALES vistos en el grupo (afirmar sin
    # probar, parchear la copia local en vez del motor compartido, anunciar en vez
    # de ejecutar, medir por atajos y decir "mejorado" sin re-comprobar la calidad).
    rigor = (
        "\n🎯 RIGOR (no negociable):\n"
        "- NO digas 'hecho/verificado/arreglado' sin PEGAR la SALIDA REAL del comando "
        "que lo prueba (texto crudo, no un resumen).\n"
        "- Si el fallo afecta a VARIAS entradas (p. ej. Telegram y la web), arregla el "
        "MOTOR COMPARTIDO (o alinea ambas) y DEMUESTRA que la otra entrada usa ese "
        "mismo motor; NO parchees solo tu copia local.\n"
        "- PROHIBIDO anunciar lo que vas a hacer ('hago un backup...', 'ahora mido...'): "
        "HAZLO con la herramienta y enseña la salida. Si una herramienta NO se ejecutó, "
        "dilo claramente en vez de dar por hecho el resultado.\n"
        "- Mide POR LA VÍA DEL USUARIO (p. ej. curl al puerto que usa él, con un caso "
        "real), no por atajos internos que no reflejan su experiencia.\n"
        "- Si mejoras VELOCIDAD, comprueba que NO empeoras la CALIDAD: pega el resultado "
        "de antes y el de después (misma entrada) y compáralos.\n"
    )
    # INFORME DE CIERRE: el humano ha pedido PARAR ("parad, dadme un resumen").
    # Un solo agente resume con EVIDENCIA y para; nada de encadenar al compañero.
    inf_txt = ""
    if informe:
        inf_txt = (
            "\n⏸ CIERRE E INFORME: el humano ha pedido PARAR y un RESUMEN.\n"
            "- Informe BREVE y VERIFICABLE: qué has hecho (con EVIDENCIA cruda: comando + "
            "salida y tiempos), qué queda pendiente y qué riesgos hay.\n"
            "- NO invoques ni menciones a tu compañero; no propongas pasos largos.\n"
            "- Termina con 'Hice:' y 'Hará:' y PARA.\n"
        )
    par_txt = ""
    if paralelo:
        par_txt = (
            f"\n⚡ PARALELO: @{otro['username']} y tú estáis trabajando AHORA MISMO, a la "
            "vez. NO toques sus archivos (respeta el CANDADO), no esperes a leer su "
            "mensaje: haz TU parte y publícala.\n"
        )
        # En paralelo NO se encadena turno: nadie tiene que "continuar" después.
        cierre = (f"- NO menciones a @{otro['username']}: estáis trabajando A LA VEZ. "
                  "Publica TU parte (Hice/Haré) y termina.\n")
    return (
        f"Eres {bot['name']} (@{bot['username']}), uno de los agentes del grupo BRAINSTORM.\n"
        "Estás en una conversación AUTO-coordinada entre agentes que comparten una PIZARRA "
        "(grupo_bus). Telegram NO entrega a un bot los mensajes de otro bot: lees lo del otro "
        "por la pizarra, no por Telegram.\n\n"
        f"{fase_txt}\nTURNO {n}/{cap}.\n\n"
        + ctx +
        "REGLAS DE ESTE TURNO (respétalas):\n"
        + reglas + cierre + rep_txt + man_txt + rigor + inf_txt + par_txt + "\n"
        "- Escribe el mensaje destinado al GRUPO (nada de meta-comentarios ni bloques de herramienta).\n"
    )


# ─────────────────────────────── trigger ─────────────────────────────────────
def _trigger(agent_id, prompt, chat, max_iters, ping=None):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(prompt)
        tmp = fh.name
    cmd = [sys.executable, os.path.join(HERE, "turno_grupo.py"),
           "--agent", agent_id, "--chat", str(chat), "--prompt-file", tmp]
    if max_iters:
        cmd += ["--max-iters", str(max_iters)]
    if ping:
        # Garantiza que el mensaje DIRIJA al compañero (visibilidad del relevo).
        cmd += ["--ping", str(ping)]
    print(f"[coordinador] 🚀 despertando {agent_id}…", flush=True)
    ok = False
    try:
        r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=600)
        tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
        print(f"[coordinador] {agent_id} → {tail[0][:200]}", flush=True)
        if r.returncode != 0:
            print((r.stderr or "")[-500:], flush=True)
        # turno_grupo.py imprime una línea JSON {ok,...}. La usamos para saber
        # si el turno REALMENTE ocurrió. Si ok=False, NO se debe consumir turno.
        try:
            ok = bool(json.loads(tail[0]).get("ok"))
        except Exception:
            ok = False
    except Exception as e:  # noqa: BLE001
        print(f"[coordinador] ⚠️ fallo al despertar {agent_id}: {e}", flush=True)
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass
    return ok


# ──────────────────── anti-BUCLE: lo decide liljoker (sin palabras fijas) ─────
BUCLE_SYS = (
    "Eres liljoker, observador de un debate entre agentes de IA que comparten una "
    "pizarra. Recibes sus ÚLTIMOS mensajes, en orden (el último es el más reciente). "
    "Decide si están en un BUCLE: repiten LO MISMO (la misma tarea, la misma "
    "conclusión tipo 'ya está cerrado', las mismas verificaciones) SIN avanzar ni "
    "aportar nada nuevo. NO es bucle si avanzan, proponen algo nuevo, reparten "
    "trabajo distinto, cambian de tema o cierran de verdad con un resultado nuevo. "
    "Responde SOLO una línea JSON: {\"bucle\": true|false, \"short\": \"motivo en 1 frase\"}."
)


def _intencion_bucle(lineas):
    """liljoker decide si los agentes están en BUCLE (repiten sin avanzar).
    lineas = lista '· autor: texto'. Si el LLM falla, NO asume bucle (False)."""
    txt = "\n".join(lineas)[-2500:]
    try:
        out, _meta = _llm_liljoker(
            [{"role": "system", "content": BUCLE_SYS},
             {"role": "user", "content": "MENSAJES DE LOS AGENTES:\n" + txt}],
            max_tokens=160)
        i, j = out.find("{"), out.rfind("}")
        data = json.loads(out[i:j + 1]) if i != -1 and j > i else {}
        return {"bucle": bool(data.get("bucle")), "short": str(data.get("short", ""))[:160]}
    except Exception as e:  # noqa: BLE001
        return {"bucle": False, "short": f"sin LLM: {e}"}


def _sin_progreso(chat, bots, n=6):
    """liljoker decide si los agentes están en BUCLE DENTRO DE LA RONDA ACTUAL.

    IMPORTANTE: solo mira los mensajes de bots POSTERIORES al último mensaje del
    humano. Si tú acabas de escribir, la ronda es NUEVA → nunca es "bucle" (antes
    miraba TODO el historial y cortaba rondas nuevas porque el pasado se repetía).
    """
    rows = _leer_board(chat)
    # índice del último mensaje que NO es de un bot (el humano u otro)
    ult_humano = -1
    for i in range(len(rows) - 1, -1, -1):
        if (rows[i].get("autor") or "").lstrip("@").lower() not in bots:
            ult_humano = i
            break
    ronda = rows[ult_humano + 1:]
    de_bots = [r for r in ronda if (r.get("autor") or "").lstrip("@").lower() in bots]
    if len(de_bots) < 4:          # hacen falta varias repeticiones DENTRO de la ronda
        return False
    lineas = []
    for r in de_bots[-n:]:
        txt = (r.get("texto") or "").replace("\n", " ").strip()[:500]
        lineas.append(f"· {r.get('autor')}: {txt}")
    return bool(_intencion_bucle(lineas).get("bucle"))


# ─────────── ¿el mensaje INVOCA a otro agente? (lo decide liljoker) ──────────
INVOCA_SYS = (
    "Eres liljoker, observador de un grupo de agentes que comparten una pizarra. "
    "Recibes el ÚLTIMO mensaje de un agente y la lista de COMPAÑEROS disponibles. "
    "Decide si ese mensaje INVOCA/PIDE a alguno de ellos que actúe, revise o le pase "
    "el turno — AUNQUE NO use '@' (p. ej. 'que lo revise mi compañero', 'V2 ¿puedes "
    "verlo?', 'paso el turno a V2', 'necesito que el otro lo compruebe'). "
    "NO es invocación si solo se despide, cierra, resume o habla al humano. "
    "Responde SOLO una línea JSON: {\"objetivo\": \"<username EXACTO de la lista, o ''>\", "
    "\"short\": \"motivo en 1 frase\"}."
)


def _invoca_otro(texto, bots, autor):
    """A qué compañero INVOCA el mensaje ('' si a ninguno). Lo decide liljoker con la
    MISMA llamada unificada `_decide` (sin depender de '@' literal)."""
    return _decide(texto, bots, autor, False).get("objetivo", "")


# ─────────────────────────────── núcleo ──────────────────────────────────────
# Tras N relevos FORZADOS seguidos (el bot no se dirigió a su compañero) se hace
# PAUSA DE VERIFICACIÓN: se corta la ronda y se le pide al HUMANO que lo pruebe,
# en vez de seguir gastando turnos hablando sin cerrar.
MAX_FORZADOS = 4


def _panel_liljoker(stt, st, n=None, cap=None, dest=None, motivo="", dime=""):
    """Mini-panel de lilJoker en el grupo: TRANSPARENCIA de lo que pasa por debajo
    (mandato vigente, fase real, relevo, por qué despierta a quién y el gasto).

    `dime` = la frase NATURAL que ya genera lilJoker en su MISMA llamada (`_decide`):
    así te "habla" explicando qué hace y por qué, pero los DATOS duros del panel
    siguen siendo los reales (el LLM no puede inventarse el estado)."""
    if not stt:
        return
    mand = " ".join(str(st.get("mandato") or "").split())[:70]
    fase = st.get("fase", "conversacion")
    modo = ("(hablando · SIN escribir)" if fase in ("conversacion", "reparto")
            else "(EJECUTANDO · escrituras permitidas)" if fase == "ejecucion"
            else "(esperando tu OK)")
    lineas = ["🤏 liljoker · coordinando…"]
    if dime:
        lineas.append(f"🗣️ {dime}")
    lineas += [f"📋 Mandato: {mand or '(ninguno)'}",
               f"🧭 Fase: {fase} {modo}"]
    if n is not None:
        lineas.append(f"🔁 Relevo {n}/{cap}")
    if dest:
        lineas.append(f"🚀 Despierto a @{dest}")
    if motivo:
        lineas.append(f"❓ Motivo: {motivo}")
    lineas.append(f"💸 {_liljoker_coste_txt()}")
    stt.pinta("\n".join(lineas))


def _decidir(st, nuevas, bots, humano, max_conv, max_exec):
    """Procesa las líneas nuevas y decide si hay que despertar a un bot.

    **liljoker es el coordinador**: UNA sola llamada (`_decide`) sobre el ÚLTIMO
    mensaje devuelve TODAS las decisiones: acción (si es humano), a quién invoca,
    consenso, bucle y si aporta algo nuevo. Devuelve info (o None)."""
    # avanzar el cursor por las líneas nuevas
    for r in nuevas:
        cur = _tsnum(r.get("ts"))
        if cur > _tsnum(st.get("cursor")):
            st["cursor"] = cur
    ult = nuevas[-1]
    autor = (ult.get("autor") or "").lstrip("@").lower()
    es_hum = _es_humano(ult.get("autor") or "", bots)
    txt = ult.get("texto") or ""
    # ── UNA sola llamada a liljoker: decide TODO ──────────────────────────────
    d = _decide(txt, bots, autor, es_hum, {
        "fase": st.get("fase"), "turnos": st.get("turnos"),
        "ultimo_humano": (st.get("ultimo_humano") or "")[:200]})
    if es_hum:
        st["ultimo_humano"] = txt
        st["forzados"] = 0            # mensaje del humano -> reinicia el contador
        acc = d["accion"]
        prev_fase = st.get("fase")
        # PARADA + INFORME: cierra el mandato (se acaban los relevos forzados),
        # frena la ronda y pide UN informe de cierre a UN solo agente (sin encadenar
        # al compañero). Es lo que pasa si dices "parad, dadme un resumen".
        if acc == "parar":
            st["fase"] = "esperando_confirmacion"
            st["mandato"] = ""
            st["reparto"] = ""
            st["reparto_ok"] = False
            st["turnos"] = 0
            st["forzados"] = 0
            st["informe"] = True
            st["ultimo_humano"] = txt
            print("[coordinador] ⏸ el humano pide PARAR + resumen (informe de cierre).",
                  flush=True)
            objetivo = d["objetivo"] or ""
            if not objetivo:
                # nadie mencionado -> lo pide el último que habló (o el primero)
                objetivo = (autor if autor in bots
                            else (list(bots)[0] if bots else ""))
            if objetivo:
                return {"objetivo": objetivo, "n": 1, "cap": max_exec, "final": False,
                        "consenso": False, "bucle": False, "avanza": False,
                        "paralelo": False, "desacuerdo": False, "reparto": "",
                        "mandato": "", "informe": True}
            return None
        # ENMIENDA A MITAD DE RONDA: si el humano escribe MIENTRAS los agentes están
        # trabajando (ronda empezada), su mensaje AMPLÍA el mandato y NO reinicia la
        # cuenta: así el compañero recibe lo nuevo sin perder lo ya hecho. Antes esto
        # reseteaba los turnos y se volvía a "despertar a seguir trabajando" desde cero.
        enmienda = (st.get("fase") in ("conversacion", "reparto")
                    and int(st.get("turnos", 0)) > 0 and acc != "ejecutar")
        if enmienda:
            previo = str(st.get("mandato") or "")
            st["mandato"] = ((previo + "\n+ EL HUMANO AÑADE: " + txt)
                             if previo else txt)[-1500:]
            print("[coordinador] 📝 enmienda al mandato (sigo la ronda, NO reinicio).",
                  flush=True)
        else:
            # MANDATO: si pide "habladlo entre vosotros / coordinaos / llegad a un
            # acuerdo", queda ABIERTO hasta que acuerden. Se guarda SU MENSAJE LITERAL
            # (no un resumen): así el compañero recibe lo pedido tal cual, sin perder
            # detalle por el camino.
            if d.get("mandato"):
                st["mandato"] = txt
            if acc in ("nueva_ronda", "conversar"):
                st["turnos"] = 0
                st["fase"] = "conversacion"
                # Tema/plan nuevo -> el reparto anterior ya no sirve.
                st["reparto"] = ""
                st["reparto_ok"] = False
                if acc == "nueva_ronda" and not d.get("mandato"):
                    st["mandato"] = ""   # tema nuevo sin mandato -> caduca el anterior
            elif acc == "ejecutar":
                st["turnos"] = 0
                st["fase"] = "ejecucion"
                st["mandato"] = ""       # autorizar/ejecutar cierra el mandato
                # Si el humano ACABA DE APROBAR un reparto ya acordado, se CONSERVA
                # (está diciendo "sí, id con ESE plan"). Si es una orden NUEVA, se
                # limpia: primero se propone el reparto para no solaparse ni pisarse.
                if not (prev_fase == "esperando_confirmacion" and st.get("reparto_ok")):
                    st["reparto"] = ""
                    st["reparto_ok"] = False
    # ¿a quién despertar? Lo dice liljoker (sin depender del '@' literal).
    objetivo = d["objetivo"] or None
    forzado = False
    if not objetivo and not es_hum and autor in bots:
        # MANDATO ABIERTO ("habladlo entre vosotros y llegad a un acuerdo"): si el
        # bot acaba de responder SIN dirigirse a su compañero y todavía no hay
        # acuerdo, lilJoker EXIGE el relevo -> se despierta al OTRO y la charla
        # sigue hasta el consenso. Antes se moría aquí (el bot informaba al humano
        # y ya no hablaba nadie más), que es justo lo que pasó.
        if (st.get("mandato") and st.get("fase") in ("conversacion", "reparto")
                and not d["consenso"] and not d["bucle"] and not d["desacuerdo"]):
            forzados = int(st.get("forzados", 0)) + 1
            st["forzados"] = forzados
            if forzados > MAX_FORZADOS:
                # PAUSA DE VERIFICACIÓN: demasiados relevos forzados seguidos sin
                # cerrar. Se CORTA y se le pide al HUMANO que lo pruebe y decida
                # (en vez de seguir gastando turnos hablando).
                print(f"[coordinador] ⏸ {forzados - 1} relevos forzados → PAUSA de "
                      "verificación (pido prueba al humano).", flush=True)
                return {"objetivo": None, "n": int(st.get("turnos", 0)), "cap": max_exec,
                        "final": False, "consenso": False, "bucle": False,
                        "avanza": False, "paralelo": False, "desacuerdo": False,
                        "reparto": "", "mandato": st.get("mandato", ""),
                        "pausa": True, "forzados": forzados - 1}
            otros = [u for u in bots if u != autor]
            objetivo = otros[0] if otros else None
            if objetivo:
                forzado = True
                print(f"[coordinador] 🔁 mandato abierto → exijo el relevo a @{objetivo} "
                      "(el anterior no se dirigió a él)", flush=True)
    # TECHO DE SEGURIDAD (liljoker decide cuándo parar; esto evita un bucle infinito).
    cap = max_exec
    if int(st.get("turnos", 0)) >= cap:
        return None
    n = int(st.get("turnos", 0)) + 1
    final = n >= cap
    # NOTA: `paralelo`, `desacuerdo`, `reparto` y `mandato` los decide lilJoker en
    # `_decide` (LLM, por INTENCIÓN, sin listas de palabras). Aquí solo se PROPAGAN.
    info = {"objetivo": objetivo, "n": n, "cap": cap, "final": final,
            "consenso": d["consenso"], "bucle": d["bucle"],
            "avanza": d["avanza"], "resumen": d["resumen"],
            "paralelo": d["paralelo"], "desacuerdo": d["desacuerdo"],
            "reparto": d["reparto"], "mandato": st.get("mandato", ""),
            "forzado": forzado, "dime": d["dime"]}
    if not objetivo and not (d["consenso"] or d["bucle"] or d["desacuerdo"]):
        return None                          # nadie a quien despertar y nada que contar
    # OJO: si NO hay objetivo pero SÍ consenso/bucle/desacuerdo, se devuelve info
    # igualmente porque `ciclo()` tiene que EVALUARLO y avisar al humano (antes se
    # devolvía None y el acuerdo se perdía en silencio).
    return info


class _Efimero:
    """Mensaje de ESTADO en el grupo que se edita y se BORRA solo (estilo OpenClaw).

    Así el humano VE que liljoker está coordinando y a quién despierta, en lugar
    de esperar a ciegas a que aparezca la respuesta del otro bot. Nunca se publica
    en la pizarra (publicar=False) -> no re-dispara turnos.
    """

    def __init__(self, bot, chat, texto=""):
        self.chat = chat
        self.mid = None
        self.tb = None
        try:
            if bot:
                from channels.telegram import TelegramBot
                self.tb = TelegramBot(bot["token"])
        except Exception:  # noqa: BLE001
            self.tb = None
        if texto:
            self.pinta(texto)

    def pinta(self, texto):
        if self.tb is None:
            return
        try:
            if self.mid is None:
                self.mid = self.tb.send(self.chat, texto)
            else:
                self.tb.edit(self.chat, self.mid, texto)
        except Exception:  # noqa: BLE001
            pass

    def borra(self):
        if self.tb is None or self.mid is None:
            return
        try:
            self.tb.delete(self.chat, self.mid)
        except Exception:  # noqa: BLE001
            pass
        self.mid = None


def ciclo(chat, bots, humano, max_conv, max_exec, dry=False, solo_ultima=False):
    st = _load_state()
    rows = _leer_board(chat)
    if not rows:
        return
    nuevas = [r for r in rows if _tsnum(r.get("ts")) > _tsnum(st.get("cursor"))]
    if solo_ultima:
        nuevas = rows[-1:]
    if not nuevas:
        return
    prev = dict(st)  # para poder REVERTIR si el despertar falla
    # Aviso EFÍMERO (se borra solo): el humano VE que liljoker está coordinando
    # en vez de esperar a ciegas. No se publica en la pizarra -> no re-dispara.
    stt = None if dry else _Efimero(next(iter(bots.values()), None), chat,
                                    "🤏 liljoker · 🔭 leyendo la pizarra… "
                                    "🧠 decidiendo qué hacer…")
    info = _decidir(st, nuevas, bots, humano, max_conv, max_exec)
    if info and info.get("pausa"):
        # PAUSA DE VERIFICACIÓN: se corta la ronda y se le pide al humano que lo
        # PRUEBE (y que decida) en vez de seguir hablando sin cerrar.
        st["fase"] = "esperando_confirmacion"
        st["forzados"] = 0
        _save_state(st)
        pedido = " ".join(str(st.get("mandato") or "").split())[:160]
        print("[coordinador] ⏸ pausa de verificación (pido prueba al humano).",
              flush=True)
        _avisar(chat, bots,
                f"⏸ liljoker: PARO aquí — lleváis {info.get('forzados')} relevos "
                "seguidos y no cerráis.\n"
                f"📋 Pedido: {pedido}\n"
                "🧪 PRUÉBALO TÚ y dime si va bien: si va, cierro; si no, diles qué "
                "falla.\n"
                "   (o di '@<bot> confirma' para darlo por bueno, o 'sigue' para que "
                "continúen).\n"
                f"💰 {_liljoker_coste_txt()}")
        if stt:
            stt.borra()
        return
    if not info:
        if stt:
            stt.borra()
        _save_state(st)
        return
    # ── liljoker (MISMA llamada): ¿ya hay CONSENSO? ¿o es un BUCLE? ──────────
    # Todo esto lo decidió `_decide` (una sola llamada). Si los agentes YA acordaron
    # o repiten sin avanzar, CORTAMOS la ronda y devolvemos el control al humano.
    ult_autor = (nuevas[-1].get("autor") or "").lstrip("@").lower()
    if not info["final"] and ult_autor in bots:
        if info.get("consenso"):
            en_reparto = st.get("fase") == "reparto"
            mand = st.get("mandato") or ""
            st["fase"] = "esperando_confirmacion"
            st["mandato"] = ""            # mandato cumplido: ya han acordado
            if en_reparto:
                # Se han puesto de acuerdo en CÓMO repartirse el trabajo: guardamos
                # el reparto y pedimos al humano que APRUEBE (no se ejecuta aún).
                st["reparto"] = info.get("reparto") or st.get("reparto", "")
                st["reparto_ok"] = True
            _save_state(st)
            objetivo = (info["objetivo"] or ult_autor
                        or (list(bots)[0] if bots else "bot"))
            print(f"[coordinador] 🤝 consenso en {info['n']}/{info['cap']} "
                  f"({info.get('resumen')}); corto la ronda.", flush=True)
            if en_reparto:
                texto = (f"🧩 liljoker: ya tienen el REPARTO acordado:\n"
                         f"{st.get('reparto') or '(sin detalle)'}\n\n"
                         f"Corto la ronda (corte de turno) para no gastar de más.\n"
                         f"¿Le doy? Di '@{objetivo} confirmo' y ejecutan ASÍ, cada uno "
                         f"SU parte (a la vez).\n"
                         f"Si prefieres otra cosa: '@{objetivo} nueva ronda: <tema>'.\n"
                         f"💰 {_liljoker_coste_txt()}")
            else:
                texto = (f"🤝 liljoker: los agentes ya han llegado a un acuerdo "
                         f"({info.get('resumen')}).\n"
                         + (f"📌 Asunto: {mand}\n" if mand else "")
                         + f"Corto la ronda (corte de turno) para no gastar de más.\n"
                         f"Escribe '@{objetivo} nueva ronda: <tema>' para seguir, o "
                         f"'@{objetivo} confirmo' para ejecutar.\n"
                         f"💰 {_liljoker_coste_txt()}")
            _avisar(chat, bots, texto)
            if stt:
                stt.borra()
            return
        if info.get("desacuerdo"):
            # No hay acuerdo: NO seguimos solos. Decide el HUMANO quién tiene razón
            # (lilJoker lo ha interpretado por intención, sin palabras fijas).
            st["fase"] = "esperando_confirmacion"
            _save_state(st)
            orden = [ult_autor] + [u for u in bots if u != ult_autor]
            opciones = "\n".join(f"· '@{u} sigue tú' → se hace como dice él"
                                 for u in orden[:2])
            print(f"[coordinador] 🤷 desacuerdo en {info['n']}/{info['cap']} "
                  f"({info.get('resumen')}); decide el humano.", flush=True)
            _avisar(chat, bots,
                    "🤷 liljoker: los agentes NO se ponen de acuerdo "
                    f"({info.get('resumen')}).\n"
                    f"@{humano}: decides TÚ. Dime, por ejemplo:\n"
                    f"{opciones}\n"
                    "(o dame tu solución y la aplican así).\n"
                    f"💰 {_liljoker_coste_txt()}")
            if stt:
                stt.borra()
            return
        if info.get("bucle"):
            st["fase"] = "esperando_confirmacion"
            _save_state(st)
            print(f"[coordinador] ⛔ bucle en {info['n']}/{info['cap']} "
                  f"({info.get('resumen')}); corto la ronda.", flush=True)
            _avisar(chat, bots,
                    "⛔ Detecto un BUCLE: los agentes repiten lo mismo sin avanzar. "
                    "Corto la ronda para no gastar tokens. Dame una instrucción NUEVA y "
                    "concreta (o cambia el enfoque) para seguir.\n"
                    f"💰 {_liljoker_coste_txt()}")
            if stt:
                stt.borra()
            return
    if not info.get("objetivo"):
        # No hay a quién despertar y ni consenso, ni bucle, ni desacuerdo: la ronda
        # simplemente espera al humano (antes se salía sin evaluar nada).
        _save_state(st)
        if stt:
            stt.borra()
        return
    bot = bots[info["objetivo"]]
    # el "otro" bot del grupo
    otros = [b for u, b in bots.items() if u != info["objetivo"]]
    otro = otros[0] if otros else bot
    # ── REPARTO PRIMERO ──────────────────────────────────────────────────────
    # Si van a trabajar en PARALELO pero todavía NO hay reparto acordado, este
    # turno NO ejecuta: se PROPONE el reparto para no solaparse ni pisarse.
    _paralelo = (bool(info.get("paralelo")) and st.get("fase") == "ejecucion"
                 and not info["final"] and len(bots) > 1)
    if _paralelo and not st.get("reparto_ok"):
        st["fase"] = "reparto"
        _save_state(st)
        print("[coordinador] 🧩 sin reparto acordado → este turno PROPONE el reparto.",
              flush=True)
    prompt = _build_prompt(bot, otro, humano, chat, st.get("fase", "conversacion"),
                           info["n"], info["cap"], info["final"],
                           reparto=st.get("reparto", ""),
                           mandato=st.get("mandato", ""),
                           informe=bool(info.get("informe")))
    print(f"\n[coordinador] decisión → fase={st.get('fase')} turno={info['n']}/{info['cap']} "
          f"final={info['final']} objetivo=@{info['objetivo']}", flush=True)
    if dry:
        print("— PROMPT QUE SE ENVIARÍA —\n" + prompt + "\n— FIN —", flush=True)
        if stt:
            stt.borra()
        return
    # ── ¿EN PARALELO? ────────────────────────────────────────────────────────
    # Lo decide liljoker (campo "paralelo"): SOLO en EJECUCIÓN y solo si los dos
    # van a ESCRIBIR/EDITAR partes distintas. Así no se esperan y la ronda va al
    # doble de rápido. En CONVERSACIÓN siempre de uno en uno (si no, se pisan y
    # repiten lo mismo), y en el turno FINAL tampoco (cierra uno solo).
    paralelo = _paralelo and bool(st.get("reparto_ok"))
    if paralelo:
        _panel_liljoker(stt, st, n=info["n"], cap=info["cap"],
                        dest=" y @".join(bots),
                        motivo="PARALELO (reparto acordado): "
                               + (st.get("reparto") or "")[:70],
                        dime=info.get("dime", ""))
        import threading
        res = {}

        def _run_par(u):
            b = bots[u]
            otros_b = [bb for uu, bb in bots.items() if uu != u]
            o = otros_b[0] if otros_b else b
            try:
                res[u] = _trigger(
                    b["id"],
                    _build_prompt(b, o, humano, chat, st.get("fase", "conversacion"),
                                  info["n"], info["cap"], info["final"], paralelo=True,
                                  reparto=st.get("reparto", "")),
                    chat, None)
            except Exception as e:  # noqa: BLE001
                print(f"[coordinador] ⚠️ paralelo {u}: {e}", flush=True)
                res[u] = False

        hilos = [threading.Thread(target=_run_par, args=(u,), daemon=True)
                 for u in bots]
        for t in hilos:
            t.start()
        for t in hilos:
            t.join(timeout=900)
        ok = any(res.values())
        print(f"[coordinador] ⚡ paralelo → {res}", flush=True)
    else:
        _panel_liljoker(stt, st, n=info["n"], cap=info["cap"], dest=info["objetivo"],
                        motivo=("el anterior no se dirigió a su compañero"
                                if info.get("forzado") else
                                "propone el REPARTO (nadie escribe todavía)"
                                if st.get("fase") == "reparto" else ""),
                        dime=info.get("dime", ""))
        inf = bool(info.get("informe"))
        # En fase de HABLAR (conversación/reparto) GARANTIZAMOS el relevo VISIBLE:
        # si el bot se olvida de mencionar a su compañero, turno_grupo lo añade.
        # (Antes el relevo ocurría solo por dentro y el humano veía informes sueltos.)
        # En INFORME de cierre NO se encadena a nadie.
        ping = None if inf else (otro["username"]
                                 if st.get("fase") in ("conversacion", "reparto")
                                 else None)
        ok = _trigger(bot["id"], prompt, chat, None, ping=ping)
    if stt:
        stt.borra()
    if ok:
        if info.get("informe"):
            # Informe entregado: ronda CERRADA. Nada de seguir encadenando turnos.
            st["informe"] = False
            st["fase"] = "esperando_confirmacion"
            _save_state(st)
            print("[coordinador] ⏸ informe entregado; ronda CERRADA (esperando al "
                  "humano).", flush=True)
            if stt:
                stt.borra()
            return
        st["turnos"] = info["n"]
        st["_fallos"] = 0
        if info["final"]:
            # El tope se agotó: la ronda queda CERRADA y el coordinador deja de
            # despertar a nadie (freno duro) hasta que el humano abra otra ronda.
            # Antes la fase de EJECUCIÓN no cambiaba -> el estado quedaba a medias
            # y cualquier mensaje la reiniciaba. Ahora AMBAS fases cierran igual.
            st["fase"] = "esperando_confirmacion"
            print(f"[coordinador] ⏹ ronda cerrada en {info['n']}/{info['cap']} "
                  f"(fase previa agotada) → esperando al humano.", flush=True)
        _save_state(st)
        if stt:
            stt.borra()
        return
    # El turno FALLÓ (LLM/Telegram). NO consumimos turno: restauramos el estado
    # previo para REINTENTAR en el próximo ciclo. Si falla 3 veces seguidas,
    # dejamos de insistir, avanzamos el cursor y avisamos al humano.
    nfallos = int(prev.get("_fallos", 0)) + 1
    st = dict(prev)
    if nfallos >= 3:
        st["cursor"] = max([_tsnum(x.get("ts")) for x in nuevas] + [_tsnum(prev.get("cursor"))])
        st["_fallos"] = 0
        _save_state(st)
        print(f"[coordinador] ⚠️ {nfallos} fallos seguidos con {info['objetivo']}; "
              "abandono esta ronda y aviso al humano.", flush=True)
        _avisar_fallo(chat, bots, humano, info["objetivo"], nfallos)
        if stt:
            stt.borra()
        return
    st["_fallos"] = nfallos
    _save_state(st)
    print(f"[coordinador] ⚠️ turno falló; no consumo turno (reintento {nfallos}/3).", flush=True)
    if stt:
        stt.borra()


def _lock():
    """Impide dos coordinadores a la vez. Devuelve el fh (o None si ya hay otro)."""
    import fcntl
    os.makedirs(BUS_DIR, exist_ok=True)
    fh = open(LOCK_F, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def main():
    ap = argparse.ArgumentParser(description="Coordinador bot<->bot (pizarra)")
    ap.add_argument("--chat", type=int, default=None)
    ap.add_argument("--humano", default="IamrealjokR")
    ap.add_argument("--bots", default="jokerv1,jokerv2",
                    help="ids (configAgentes.json) que participan en el diálogo")
    _me = os.environ.get("TW_MAX_EXEC", "12")
    ap.add_argument("--max-exec", type=int, default=int(_me) if str(_me).isdigit() else 12,
                    help="TECHO DE SEGURIDAD de turnos por ronda (def 12). lilJoker decide "
                         "cuándo parar (consenso/bucle); esto solo evita un bucle infinito.")
    ap.add_argument("--max-conv", type=int, default=0,
                    help="(obsoleto) antes limitaba la conversación; ya NO se usa")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--simular", metavar="TEXTO",
                    help="prueba: simula esa línea del humano y muestra el prompt")
    args = ap.parse_args()

    chat = args.chat or _detectar_chat()
    if not chat:
        print("No hay pizarra en grupo_bus/chat_*.jsonl. Pasa --chat <id>.")
        sys.exit(1)

    permitidos = {s.strip() for s in (args.bots or "").split(",") if s.strip()}
    bots = _bots(permitidos)
    print(f"[coordinador] grupo={chat} · bots={ {u: v['id'] for u, v in bots.items()} } "
          f"· humano=@{args.humano}", flush=True)

    if args.simular:
        st = _load_state()
        # forzamos una conversación nueva como si el humano acabara de escribir eso
        st["cursor"] = time.time()  # nada nuevo real
        st["turnos"] = 0
        st["fase"] = "conversacion"
        st["ultimo_humano"] = args.simular
        row = {"ts": time.time(), "autor": "@" + args.humano, "texto": args.simular}
        info = _decidir(st, [row], bots, args.humano, args.max_conv, args.max_exec)
        if not info:
            print("Sin mención a ningún bot -> no se despertaría a nadie.")
            return
        bot = bots[info["objetivo"]]
        otros = [b for u, b in bots.items() if u != info["objetivo"]]
        otro = otros[0] if otros else bot
        prompt = _build_prompt(bot, otro, args.humano, chat, st["fase"],
                               info["n"], info["cap"], info["final"])
        print(f"→ despertaría a @{info['objetivo']} (turno {info['n']}/{info['cap']})")
        print("\n— PROMPT —\n" + prompt + "— FIN —")
        return

    if not args.dry_run:
        fh = _lock()
        if fh is None:
            print("[coordinador] ya hay otro coordinador activo. Salgo.")
            sys.exit(0)
    if args.dry_run:
        ciclo(chat, bots, args.humano, args.max_conv, args.max_exec, dry=True)
        return
    print("[coordinador] vigilando la pizarra… (Ctrl+C para salir)", flush=True)
    while True:
        try:
            ciclo(chat, bots, args.humano, args.max_conv, args.max_exec)
        except KeyboardInterrupt:
            print("\n[coordinador] parado a mano.")
            break
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[coordinador] ⚠️ {e}\n{traceback.format_exc()[:600]}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
