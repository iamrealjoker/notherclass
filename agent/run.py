"""
run.py — punto de entrada del agente NotherClass (nuestro "OpenClaw con nuestro cerebro").

Modos:
  python run.py --oneshot "mensaje"        # un turno por CLI (prueba sin Telegram)
  python run.py                            # daemon: escucha Telegram (tu móvil)

Ciclo por turno (la conciencia):
  recordar (recall híbrido + working memory) → construir system prompt curado →
  pedir al LLM → ejecutar sus skills (tools) en bucle → guardar lo vivido en
  memoria episódica (chunks) → aprender si el humano marca win/fail.
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import audio
from core import agentconfig
from core import context as context_mod
from core import llm, skills, supervisor
from core.logger import ActivityLog
from brain import Brain, consolidate as consol_mod, embeddings
from channels.telegram import TelegramBot

# Cargar .env (estilo proyecto)
HERE = os.path.dirname(os.path.abspath(__file__))

# ── API LOCAL de turnos (para la WEB): el MISMO agente, en el MISMO proceso ──
# El chat de la web (consola) no puede usar este runtime si no se lo exponemos.
# Aquí se levanta un micro-servidor SOLO en 127.0.0.1 (no publicado):
#     POST /turn   {"texto": "..."} -> {ok, respuesta, herramientas, pasos, seg, coste}
#     GET  /health                  -> {ok, agente}
# Y va con LOCK: la web y Telegram NUNCA se pisan (un turno cada vez).
_TURNO_LOCK = threading.Lock()

# Estado EFÍMERO del turno en curso: la WEB lo consulta (GET /estado) para pintar
# QUÉ hace el agente EN VIVO (burbuja que desaparece al llegar la respuesta),
# igual que el mensaje efímero de Telegram. Un estado por proceso = por agente.
_ESTADO = {"activo": False, "texto": "", "inicio": 0.0, "pasos": 0, "log": []}


def _servidor_turno(agent, puerto):
    """Escucha en 127.0.0.1:<puerto> y ejecuta turnos REALES de ESTE agente."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):        # silencio: que no llene el log
            pass

        def _json(self, code, obj):
            cuerpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            if self.path.startswith("/health"):
                self._json(200, {"ok": True, "agente": agent.name, "puerto": puerto})
            elif self.path.startswith("/estado"):
                # Estado del turno EN CURSO (para el mensaje efímero de la web).
                seg = round(time.time() - _ESTADO["inicio"], 1) if _ESTADO["activo"] else 0.0
                self._json(200, {"ok": True, "agente": agent.name,
                                 "activo": bool(_ESTADO["activo"]),
                                 "texto": _ESTADO["texto"] if _ESTADO["activo"] else "",
                                 "seg": seg, "pasos": _ESTADO["pasos"]})
            else:
                self._json(404, {"error": "no encontrado"})

        def do_POST(self):
            if not self.path.startswith("/turn"):
                self._json(404, {"error": "no encontrado"})
                return
            try:
                largo = int(self.headers.get("Content-Length") or 0)
                datos = json.loads(self.rfile.read(largo) or b"{}")
            except Exception as e:  # noqa: BLE001
                self._json(400, {"error": f"json: {e}"})
                return
            texto = (datos.get("texto") or "").strip()
            if not texto:
                self._json(400, {"error": "texto vacío"})
                return
            pasos = []

            def _hook(name, args, result):
                # Telemetría igual que en Telegram, y ADEMÁS publicada en /estado
                # para que la web la muestre EN VIVO mientras el turno trabaja.
                try:
                    d = _rich_desc(name, args or {})
                    if result is None:
                        estado = f"{d}…"
                    else:
                        _ESTADO["pasos"] += 1
                        prev = _rich_result(result)
                        estado = f"{d}\n\n{prev}" if prev else f"{d} ✓"
                    _ESTADO["texto"] = estado
                    _ESTADO["log"].append(estado)   # traza completa (desplegable web)
                    pasos.append({"tool": name, "desc": d, "ok": result is not None})
                except Exception:  # noqa: BLE001
                    pass

            t0 = time.time()
            _ESTADO.update({"activo": True, "texto": "🧠 pensando…",
                            "inicio": time.time(), "pasos": 0, "log": []})
            try:
                with _TURNO_LOCK:                    # un turno a la vez (web/Telegram)
                    anterior = agent.tool_hook
                    agent.tool_hook = _hook
                    try:
                        out, stats = agent.run_turn(texto, ctx={"web": 1})
                    finally:
                        agent.tool_hook = anterior
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)[:300]})
                return
            finally:
                _ESTADO["activo"] = False
            self._json(200, {
                "ok": True, "agente": agent.name, "respuesta": out or "",
                "herramientas": (stats or {}).get("tools_used", []),
                "pasos": pasos[:40], "trazado": list(_ESTADO["log"]),
                "seg": round(time.time() - t0, 1),
                "coste": ((stats or {}).get("usage") or {}).get("cost")})

    srv = ThreadingHTTPServer(("127.0.0.1", int(puerto)), _Handler)
    print(f"[http] 🔌 API local de turnos en http://127.0.0.1:{puerto} "
          f"(la WEB usa ESTE agente)", flush=True)
    srv.serve_forever()

llm._load_dotenv(os.path.join(HERE, ".env"))


def cfg(key, default=""):
    return os.environ.get(key, default) or default


def cfg_int(key, default=0):
    """Lee un entero del .env tolerando comentarios en la misma línea (# …)."""
    v = cfg(key, str(default))
    v = v.split("#", 1)[0].strip()
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


def _safe_args(name, args):
    """Descripción corta y segura de los args de una tool para log/telemetría."""
    if not isinstance(args, dict):
        return str(args)[:80]
    if name in ("run_shell",) and args.get("command"):
        return f'command="{args["command"][:80]}"'
    if name in ("write_file", "read_file", "list_dir") and args.get("path"):
        return f'path="{args["path"]}"'
    if name == "write_file":
        return f'path="{args.get("path")}" ({len(str(args.get("content", "")))} chars)'
    if name in ("recall", "remember") and args.get("query", args.get("text")):
        return str(args.get("query") or args.get("text"))[:80]
    return json.dumps(args, ensure_ascii=False)[:80]


# ── FINAL robusto e independiente del idioma ────────────────────────────────
# El agente cierra su mensaje FINAL emitiendo un marcador estructural fijo
# (no frases en un idioma): una línea ```final. Si trabaja con tools y termina
# SIN ese marcador, el bucle no cierra y le pide completar/cerrar con él.
FINAL_MARK = "```final"

SYSTEM_REVIEWER = """Eres BIGBOSS, un revisor senior MUY crítico y COLD.
Recibes DATOS CRUDOS (la tarea y el contenido real de los archivos que Joker tocó),
NO la narrativa de Joker. El resumen de Joker NO es autoridad: verifícalo contra los
archivos. Juzga si lo hecho es un PARCHE (ataja el síntoma) o ROBUSTO (corrige la causa raíz).
Checklist:
1. ¿Cuál es la CAUSA RAÍZ del problema? (no el síntoma)
2. ¿Lo que Joker cambió corrige la causa, o solo un caso / un valor fijo?
3. ¿Qué otros casos, idiomas o efectos secundarios podría romper?
4. ¿Existe una solución estructuralmente mejor, menos invasiva y más general?
Si para decidir necesitas ver OTRO archivo que no te mostré, responde en tu PRIMERA
línea exactamente: CONTEXTO: <ruta/al/archivo>
y nada más. Te lo leeré y me lo vuelves a evaluar. NO adivines el contenido de un archivo
que no viste. Cuando ya tengas lo necesario, termina EXACTAMENTE con:
VEREDICTO: OK  (si es robusta)
o
VEREDICTO: REVISAR  (si es un parche o tiene riesgo)
Razón: ...  (concisa, y termina en un punto — no la cortes)
Sugerencia: ...  (qué cambiarías, concreta)
RECOMENDACIÓN: 1 frase accionable con TU opción preferida ("yo haría X", por ejemplo: dividir
en partes, definir antes el contrato, no sobreescribir sin verificar, etc.)
Sé duro, concreto y no inventes problemas. Al final SIEMPRE da una RECOMENDACIÓN concreta."""
_REVIEW_CTX_RE = re.compile(r"CONTEXTO:\s*([^\s]+)", re.IGNORECASE)


def _read_for_review(workspace: str, path: str, limit: int = 20000) -> str:
    """Lee un archivo dentro del workspace para BigBoss (contenido real, "cold").
    Límite alto: BigBoss debe poder leer archivos completos para opinar sin sesgo."""
    try:
        base = os.path.abspath(workspace)
        target = os.path.abspath(os.path.join(base, path))
        if not target.startswith(base + os.sep) and target != base:
            return "(ruta fuera del workspace; no se lee)"
        if not os.path.isfile(target):
            return "(no existe / no es archivo)"
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
        if len(txt) > limit:
            return txt[:limit] + f"\n…[recortado: {len(txt)} chars totales]"
        return txt
    except Exception as e:  # noqa: BLE001
        return f"(error al leer: {e})"


def _bb_neto(raw: str, maxlen: int = 380) -> str:
    """Resumen 'neto' de BigBoss: prioriza su RECOMENDACIÓN; si no, la primera
    frase completa de su Razón. Sin cortar a media palabra."""
    t = (raw or "").strip()
    rec = ""
    m = re.search(r"(?im)^RECOMENDACI[ÓO]N\s*:?\s*(.+)$", t)
    if m:
        rec = m.group(1).strip()
    if not rec:
        rm = re.search(r"(?im)^RAZ[ÓO]N\s*:?\s*(.+)$", t)
        if rm:
            frase = rm.group(1).strip()
            idx = frase.find(". ")
            rec = (frase[:idx + 1] if idx != -1 else frase[:160]).strip()
    if not rec:
        rec = t[:maxlen]
    rec = rec.replace("\n", " ").strip()
    if len(rec) > maxlen:
        rec = rec[:maxlen].rstrip() + "…"
    return rec


def _trim_tool(out, cap=None):
    """Recorta el resultado de una tool que se reinyecta al contexto (soft-trim).
    Conserva CABEZA **y COLA** (los errores/resúmenes suelen estar al FINAL), no
    solo el principio: así el agente ve el fallo y no repite el comando a ciegas."""
    if not out:
        return out
    if cap is None:
        cap = cfg_int("TW_TOOL_OUT_CAP", 1800)
    if len(out) <= cap:
        return out
    head = int(cap * 0.45)
    tail = cap - head
    return (out[:head]
            + f"\n…[recortado el medio: {len(out) - cap} chars de {len(out)}; si necesitas "
              f"el detalle usa grep, o filtra con head/tail]…\n"
            + out[-tail:])


def _stub_native_calls(tc_list):
    """Devuelve una COPIA de los tool_calls con el `content` de write_file/append_file
    sustituido por un stub. Así el historial que se reenvía al modelo en cada
    iteración NO repite el contenido entero del archivo (el archivo YA está en
    disco y el modelo puede releerlo con read_file). Ahorra muchísimos tokens."""
    import copy as _copy
    out = []
    for tc in (tc_list or []):
        tc2 = _copy.deepcopy(tc)
        fn = tc2.get("function") or {}
        if fn.get("name") in ("write_file", "append_file"):
            try:
                a = json.loads(fn.get("arguments") or "{}")
            except Exception:
                a = None
            if isinstance(a, dict) and "content" in a:
                n = len(a.get("content") or "")
                a["content"] = (f"[omitido en el historial: {n} chars; el archivo YA está en "
                                f"disco en '{a.get('path', '?')}'. Usa read_file si necesitas "
                                f"verlo o editarlo.]")
                fn["arguments"] = json.dumps(a, ensure_ascii=False)
        out.append(tc2)
    return out


def _podar_historial(msgs, keep_recent=None, cap=None, maxchars=None):
    """Context management (estilo Cline): si el historial crece MUCHO, mantiene
    COMPLETOS los últimos `keep_recent` resultados de tool y ELIDE (a una línea)
    los antiguos grandes. Solo actúa al pasar `maxchars` para NO romper el caché
    de prompt (DeepSeek cachea el prefijo; reescribir el historial lo invalida)."""
    if keep_recent is None:
        keep_recent = cfg_int("TW_HIST_KEEP", 4)
    if cap is None:
        cap = cfg_int("TW_HIST_CAP", 500)
    if maxchars is None:
        maxchars = cfg_int("TW_HIST_MAXCHARS", 160000)
    total = sum(len(str(m.get("content") or "")) for m in msgs)
    if total <= maxchars:
        return                      # no tocamos: preserva el caché del prefijo
    idx = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
    if keep_recent > 0:
        idx = idx[:-keep_recent]
    for i in idx:
        c = msgs[i].get("content") or ""
        if len(c) > cap:
            msgs[i]["content"] = (c[:200]
                                  + f" …[resultado antiguo elidido: {len(c)} chars; "
                                    f"usa grep o read_file si lo necesitas]")


# Emoji por HERRAMIENTA: de un vistazo se ve QUÉ está haciendo el bot
# (🔭 buscando, ✍️ escribiendo, 🖥️ ejecutando…). Se usa en los mensajes EFÍMEROS
# de estado (chat privado, grupo) y en el pie de uso.
_EMOJI_TOOL = {
    "run_shell": "🖥️",        # ejecuta comandos
    "grep": "🔭",             # busca con binoculares
    "read_file": "📖",        # lee un archivo
    "write_file": "✍️",       # mano escribiendo
    "append_file": "✍️",      # sigue escribiendo
    "edit_file": "✍️",        # edita lo justo (str_replace)
    "list_dir": "📂",         # mira carpetas
    "mapa": "🗺️",             # mapa del código
    "recall": "🧠",           # consulta su memoria
    "remember": "💾",         # guarda en memoria
    "learn": "🎓",            # aprende una lección
    "set_belief": "🧩",       # fija una creencia
    "show_lessons": "📚",     # repasa lecciones
    "crear_agente": "🤖",     # crea otro agente
    "eliminar_agente": "🗑️",
}


def _emoji_tool(name):
    """Emoji que representa la herramienta (para los efímeros y el pie de uso)."""
    return _EMOJI_TOOL.get((name or "").strip().lower(), "🛠️")


def _rich_desc_txt(name, args):
    """Descripción CLARA y poco truncada de qué hace la herramienta (para el efímero)."""
    a = args or {}
    if name == "run_shell":
        return "ejecutando: " + str(a.get("command", ""))
    if name == "read_file":
        p = a.get("path", "?")
        if a.get("start") is not None or a.get("end") is not None:
            return f"leyendo {p} (líneas {a.get('start','-')}–{a.get('end','-')})"
        return f"leyendo {p}"
    if name == "grep":
        d = f"buscando '{a.get('pattern', '?')}'"
        if a.get("path"):
            d += f" en {a['path']}"
        if a.get("glob"):
            d += f" ({a['glob']})"
        return d
    if name == "edit_file":
        return f"editando {a.get('path', '?')}"
    if name == "list_dir":
        return f"mirando la carpeta {a.get('path', '.')}"
    if name == "mapa":
        return f"mapa del código: {a.get('path', '.')}"
    if name == "write_file":
        return f"escribiendo {a.get('path')} ({len(str(a.get('content', '')))} chars)"
    if name == "append_file":
        return f"añadiendo a {a.get('path')} (+{len(str(a.get('content', '')))} chars)"
    if name in ("recall", "remember"):
        return "consultando mi memoria"
    if name == "learn":
        return f"apuntando lección: {str(a.get('title', ''))[:60]}"
    if name == "set_belief":
        return f"fijando creencia: {a.get('key', '')}"
    if name == "show_lessons":
        return "repasando lecciones"
    if name == "crear_agente":
        return f"creando el agente {a.get('id', '?')}"
    if name == "eliminar_agente":
        return f"eliminando el agente {a.get('id', '?')}"
    return name


def _rich_desc(name, args):
    """Como _rich_desc_txt pero con el EMOJI de la herramienta DELANTE, para que
    los mensajes efímeros digan de un vistazo qué se está haciendo."""
    return f"{_emoji_tool(name)} {_rich_desc_txt(name, args)}"


def _rich_result(result, cap=700):
    """Trozo legible del resultado de la tool para el efímero."""
    if not result:
        return ""
    r = str(result).strip()
    if len(r) <= cap:
        return r
    # para outputs grandes mostramos el inicio (y un aviso)
    return r[:cap] + "\n…[resultado más largo]"


# ─── confirmación de edición ("pausa real") ────────────────────────────────
# NOTA: la intención del humano se interpreta con la PROPIA IA (cualquier idioma),
# no con listas de palabras fijas. Ve _classify_confirmation en Agent.


def _mk_stats(model: str = "?") -> dict:
    return {"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                      "cache_hit": 0, "cost": 0.0, "calls": 0},
            "tools_used": [], "model": model,
            "mem_sent_tokens": 0, "budget_tokens": 0, "review": None}


class Agent:
    def __init__(self, name=None):
        self.name = name or cfg("TW_AGENT_NAME", "Forja")
        self.store_id = cfg("TW_AGENT_STORE", "") or self.name  # clave estable de la BD
        workspace = cfg("TW_WORKSPACE", "../")
        self.workspace = os.path.abspath(os.path.join(HERE, workspace))
        memory_dir = cfg("TW_MEMORY_DIR", "MEMORY")
        if not os.path.isabs(memory_dir):
            memory_dir = os.path.join(HERE, memory_dir)
        persona_path = cfg("TW_AGENT_PERSONA", "AGENTS/notherclass-joker.md")
        if not os.path.isabs(persona_path):
            persona_path = os.path.join(HERE, persona_path)
        self.persona_path = persona_path
        self.budget = cfg_int("TW_CONTEXT_BUDGET_TOKENS", 2800)
        # Techo de salida del modelo: alto para poder escribir archivos grandes de una
        # sola vez (es un tope, no un objetivo). El prompt pide ser conciso.
        self.max_out = cfg_int("TW_AGENT_MAX_TOKENS", 8000)
        self.brain = Brain(memory_dir, agent_id=self.store_id)
        self.history: list = []
        self.log = ActivityLog()
        self.tool_hook = None   # opcional: callable(name, args, result|None) para telemetría
        # ── confirmación de edición: Joker propone y espera tu sí/no antes de escribir ──
        self.confirm = str(cfg("TW_CONFIRM_EDITS", "1")).lower() in ("1", "yes", "on", "true")
        # ── ¿este agente NO debe pedir NUNCA la 2ª opinión de BigBoss? ──
        # jokerv1 y jokerv2 (agentes del grupo BRAINSTORM) trabajan SIN revisor:
        # NUNCA invocan a BigBoss ni ofrecen su segunda opinión. Se decide por
        # CÓDIGO (no por prompt) y SOLO les afecta a ellos; el resto de agentes
        # mantiene su revisor intacto.
        self.sin_bigboss = self.store_id.strip().lower() in ("jokerv1", "jokerv2")
        _slug = re.sub(r"\W+", "_", self.store_id)
        self._pending_file = os.path.join(HERE, f"pending_{_slug}.json")
        self._pending = self._load_pending()
        self._spend = {}   # gasto por rol en el turno actual: joker / bigboss / liljoker / otros
        self._seed_identity()
        self.log.write("boot", {"agent": self.name, "store": self.store_id,
                                "workspace": self.workspace})

    def _seed_identity(self):
        if not self.brain.get_belief("identity.role"):
            self.brain.set_belief(
                "identity.role",
                "Asistente autónomo con memoria persistente y acceso a herramientas",
                0.99)
        if not self.brain.get_belief("project.root"):
            self.brain.set_belief("project.root", self.workspace, 0.99)

    def _deps(self):
        return {"brain": self.brain, "workspace": self.workspace}

    # ── ediciones pendientes (pausa real: se escriben solo tras tu sí/no) ──
    def _load_pending(self) -> dict:
        try:
            if os.path.exists(self._pending_file):
                with open(self._pending_file, "r", encoding="utf-8") as fh:
                    return json.load(fh)
        except Exception:  # noqa: BLE001
            pass
        return {}

    def _save_pending(self):
        try:
            with open(self._pending_file, "w", encoding="utf-8") as fh:
                json.dump(self._pending, fh, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            self.log.write("error", {"message": "no se pudo guardar pending"})

    def has_pending(self, chat_id: str) -> bool:
        return bool(self._pending.get(chat_id))

    def _drop_pending(self, chat_id: str):
        self._pending.pop(chat_id, None)
        self._save_pending()

    def _review_staged(self, task: str, staged: list, plan: str = "") -> tuple:
        """BigBoss evalúa la propuesta SIN escribir aún (cold, con el contenido real que
        Joker quiere poner). Devuelve (revisar: bool, mensaje, meta)."""
        from core import llm as _llm
        model = cfg("TW_REVIEW_MODEL", "") or "deepseek-v4-pro"
        base_url = cfg("TW_REVIEW_BASE_URL", "") or "https://api.deepseek.com/v1/chat/completions"
        api_key = cfg("TW_REVIEW_API_KEY", "")
        rev_max_tokens = cfg_int("TW_REVIEW_MAX_TOKENS", 12000)
        max_fetches = cfg_int("TW_REVIEW_MAX_FETCHES", 3)
        is_kimi = "moonshot.ai" in base_url
        meta = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0,
                "seg": 0.0, "rounds": 1, "model": model, "criticas": []}

        def _acc(m):
            u = m["usage"]
            meta["calls"] += 1
            meta["prompt_tokens"] += u["prompt_tokens"]
            meta["completion_tokens"] += u["completion_tokens"]
            meta["cost"] += m["cost"]
            meta["seg"] += m.get("elapsed", 0.0) or 0.0

        def _bb(msgs):
            return _llm.chat_verbose(
                msgs, temperature=0.2, max_tokens=rev_max_tokens, model=model,
                base_url=base_url, api_key=api_key,
                price_provider="kimi" if is_kimi else None)

        # contenido propuesto (el "diff" a aplicar), sin haber tocado disco
        partes = [f"TAREA del usuario:\n{task[:3000]}"]
        if plan:
            partes.append(f"\nOBJETIVO que Joker persigue (su plan; NO es autoridad):\n{plan[:800]}")
        partes.append("\nCAMBIO PROPUESTO por Joker (AÚN NO aplicado):")
        rutas = []
        for op in staged:
            tag = "crear/sobrescribir" if op["op"] == "write_file" else "añadir al final"
            partes.append(f"\n--- {op['path']} ({tag}) ---")
            partes.append(op.get("content", "")[:9000])
            if op["path"] not in rutas:
                rutas.append(op["path"])
        partes.append("\nARCHIVOS que puedes leer COMPLETOS pidiendo 'CONTEXTO: <ruta>': " +
                      ", ".join(rutas))
        partes.append("\n(El resumen de Joker NO es autoridad: lee tú los archivos que necesites "
                      "antes de opinar. Si un archivo es grande y no te cabe, pide CONTEXTO del "
                      "mismo o de sus dependencias en varias llamadas.)")
        datos = "\n".join(partes)
        msgs = [{"role": "system", "content": SYSTEM_REVIEWER},
                {"role": "user", "content": datos}]
        for _ in range(max_fetches):
            v, m = _bb(msgs)
            _acc(m)
            need = _REVIEW_CTX_RE.findall(v)
            if need and len(v.strip()) <= 120:
                p = need[0]
                msgs.append({"role": "assistant", "content": v})
                msgs.append({"role": "user",
                             "content": f"[BigBoss pidió contexto] {p}:\n"
                                        f"{_read_for_review(self.workspace, p)}\n\n"
                                        f"Ahora evalúa y da tu VEREDICTO final."})
                continue
            break
        revisar = "REVISAR" in v.upper()
        if revisar:
            meta["criticas"].append(v)
            meta["sin_consenso"] = True
        # neto legible (frase completa + recomendación, no cortada a media palabra)
        if revisar:
            msg = ("👑 BigBoss lo marcó REVISAR. Su opinión: " + _bb_neto(v) +
                   "\n(recomendación/razonamiento completo en auditoría)")
        else:
            msg = v[:500]
        return revisar, msg, meta

    def _spend_init(self):
        self._spend = {}

    def _spend_add(self, who: str, meta: dict):
        u = meta.get("usage") or {}
        s = self._spend.setdefault(
            who, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                  "total_tokens": 0, "cache_hit": 0, "cost": 0.0, "model": "?",
                  "seg": 0.0})
        s["calls"] += 1
        s["prompt_tokens"] += u.get("prompt_tokens", 0)
        s["completion_tokens"] += u.get("completion_tokens", 0)
        s["total_tokens"] += u.get("total_tokens", 0)
        s["cache_hit"] += u.get("prompt_cache_hit_tokens", 0)
        s["cost"] += meta.get("cost", 0.0)
        s["seg"] += meta.get("elapsed", 0.0) or 0.0
        if meta.get("model"):
            s["model"] = meta["model"]

    def _spend_merge(self, who: str, s: dict):
        """Suma un resumen ya agregado (p. ej. review_meta) a un bucket por rol."""
        if not s:
            return
        d = self._spend.setdefault(
            who, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                  "total_tokens": 0, "cache_hit": 0, "cost": 0.0, "model": "?",
                  "seg": 0.0})
        pt = s.get("prompt_tokens", 0)
        ct = s.get("completion_tokens", 0)
        d["calls"] += s.get("calls", 0)
        d["prompt_tokens"] += pt
        d["completion_tokens"] += ct
        d["total_tokens"] += s.get("total_tokens", pt + ct)
        d["cache_hit"] += s.get("cache_hit", 0)
        d["cost"] += s.get("cost", 0.0)
        d["seg"] += s.get("seg", 0.0) or 0.0
        if s.get("model"):
            d["model"] = s["model"]

    def _quiere_bigboss(self, user_text: str) -> dict:
        # liljoker interpreta la INTENCION del humano (cualquier idioma o frase):
        # ¿esta pidiendo que BigBoss revise el ultimo cambio de Joker?
        _sys = ("Eres liljoker, interprete de intencion de un agente de codigo. "
                "El humano escribe un mensaje; decide si ESTA PIDIENDO que BigBoss "
                "(el agente revisor) verifique el ultimo cambio que hizo Joker. "
                "Responde SOLO una linea JSON: {\"quiere_review\": true|false, "
                "\"short\": \"motivo en 1 frase, en el idioma del humano\"}. "
                "quiere_review=true SOLO ante una peticion clara de revision/segunda "
                "opinion/verificacion de los cambios hechos, con CUALQUIER frase: "
                "'que BigBoss lo verifique', 'quiero que lo revise', 'pasalo por "
                "bigboss', 'que le eche un ojo al cambio', 'revisalo', 'verifica el "
                "cambio', 'que bigboss lo valide', 'que lo chequee'. "
                "quiere_review=false en todo lo demas, INCLUIDO pedirle a Joker que "
                "revise logs/archivos/web/codigo (eso se lo pides a Joker para "
                "investigar, no a BigBoss), 'revisa' a secas, ordenes normales de "
                "trabajo ('haz x', 'continua', 'aplica'), preguntas y charla normal. "
                "Ante la duda, elige false.")
        try:
            txt, _meta = llm.chat_verbose(
                [{"role": "system", "content": _sys},
                 {"role": "user",
                  "content": "MENSAJE DEL HUMANO:\n" + (user_text or "")[:800]}],
                temperature=0.0, max_tokens=220)
            self._spend_add("liljoker", _meta)
            i, j = txt.find("{"), txt.rfind("}")
            data = json.loads(txt[i:j + 1]) if i != -1 and j > i else {}
            return {"quiere_review": bool(data.get("quiere_review")),
                    "short": str(data.get("short", ""))[:160]}
        except Exception:  # noqa: BLE001
            return {"quiere_review": False, "short": ""}

    def _classify_confirmation(self, user_text: str, pending_rec: dict) -> dict:
        """Entiende EN CUALQUIER IDIOMA qué quiere el humano sobre la edición pendiente.
        Devuelve {"action": ..., "short": ...} con action en:
        apply_direct | apply_with_review | cancel | revise | new_task."""
        _sys = ("Eres el intérprete de confirmación de un agente de código. Debes entender el "
                "mensaje del humano EN CUALQUIER IDIOMA y decidir su intención sobre un cambio "
                "que el agente propuso (aún NO aplicado). Responde SOLO una línea JSON:\n"
                "{\"action\": \"apply_direct\" | \"apply_with_review\" | \"cancel\" | "
                "\"revise\" | \"new_task\", \"short\": \"porqué en 1 frase, en el idioma del humano\"}\n"
                "Reglas: apply_direct = autoriza aplicar SIN revisión; apply_with_review = "
                "quiere/acepta una segunda opinión (BigBoss) antes; cancel = rechaza / no quiere "
                "que se haga; revise = pide cambios o aclaraciones a la propuesta; new_task = "
                "habla de otra cosa distinta a confirmar. Si hay ambigüedad entre cancel y "
                "aplicar-directo, elige la más probable y explica en short.")
        resumen = "CAMBIO PROPUESTO (aún no aplicado):\n" + "\n".join(
            f"- {s.get('op')} {s.get('path')}" for s in pending_rec.get("staged", [])[:6])
        try:
            txt, _meta = llm.chat_verbose(
                [{"role": "system", "content": _sys},
                 {"role": "user",
                  "content": resumen + "\n\nMENSAJE DEL HUMANO:\n" + (user_text or "")}],
                temperature=0.0, max_tokens=600)
            self._spend_add("liljoker", _meta)
            i, j = txt.find("{"), txt.rfind("}")
            data = json.loads(txt[i:j + 1]) if i != -1 and j > i else {}
            act = data.get("action", "new_task")
            if act not in ("apply_direct", "apply_with_review", "cancel", "revise", "new_task"):
                act = "new_task"
            return {"action": act, "short": str(data.get("short", ""))[:160]}
        except Exception:  # noqa: BLE001
            return {"action": "new_task", "short": ""}

    def _apply_pending(self, chat_id: str, with_bigboss: bool):
        """Aplica la edición que estaba en pausa.

        - with_bigboss=True  → corre BigBoss. Si OK, aplica. Si REVISAR, NO aplica y
          mantiene la edición pendiente para que el humano decida.
        - with_bigboss=False → aplica directo (tras 'no' o tras 'aplica igual/forzar')."""
        rec = self._pending.get(chat_id)
        if not rec or not rec.get("staged"):
            self._pending.pop(chat_id, None)
            self._save_pending()
            return "No hay ningún cambio pendiente para aplicar.", self._stats_from_roles()
        staged = rec["staged"]
        task = rec.get("task", "")
        plan = rec.get("plan", "")

        if with_bigboss:
            revisar, msg, meta = self._review_staged(task, staged, plan)
            self._spend_merge("bigboss", meta)
            if revisar:
                rec["revisado"] = True
                rec["review_neto"] = msg
                self._save_pending()          # NO se descarta: el humano decide luego
                return ("✋ No apliqué el cambio todavía (no se escribió nada).\n\n" + msg +
                        "\n\nResponde: 'aplica' (aplicarlo igual), 'corrige' (Joker lo arregla "
                        "siguiendo a BigBoss) o 'descarta'."), self._stats_from_roles()

        # aquí aplicamos: o directo (no/quiere) o tras OK de BigBoss o 'aplica igual'
        self._pending.pop(chat_id, None)
        self._save_pending()
        notas = []
        for op in staged:
            out = skills.execute_tool_call(op["op"],
                                           {"path": op["path"], "content": op.get("content", "")},
                                           self._deps())
            notas.append(f"- {op['op']} {op['path']}: {str(out)[:160]}")
        applied = "✅ Aplicado.\n" + "\n".join(notas)
        if not with_bigboss:
            applied += "\n(Sin segunda opinión de BigBoss, como pediste / forzado.)"
        return applied, self._stats_from_roles()

    def _stats_from_roles(self):
        """Construye stats (usage total + roles) a partir del acumulador self._spend."""
        tot = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
               "cache_hit": 0, "cost": 0.0, "calls": 0}
        for s in self._spend.values():
            tot["prompt_tokens"] += s.get("prompt_tokens", 0)
            tot["completion_tokens"] += s.get("completion_tokens", 0)
            tot["total_tokens"] += s.get("total_tokens", 0)
            tot["cache_hit"] += s.get("cache_hit", 0)
            tot["cost"] += s.get("cost", 0.0)
            tot["calls"] += s.get("calls", 0)
        model = next(iter(self._spend.values()), {}).get("model", "?")
        return {"usage": tot, "tools_used": [], "model": model,
                "mem_sent_tokens": 0, "budget_tokens": 0,
                "review": None, "roles": dict(self._spend)}

    def run_turn(self, user_query: str, max_iters: int = None, ctx: dict = None):
        chat_id = ctx.get("chat_id", "default") if isinstance(ctx, dict) else "default"
        if not max_iters:
            max_iters = cfg_int("TW_MAX_ITERS", 12)   # menos idas y vueltas = menos gasto
        self._spend_init()   # gasto por rol se reinicia en cada turno (joker/bigboss/liljoker)

        # liljoker interpreta UNA vez por turno si el humano pide que BigBoss
        # revise (por intencion, sin diccionario de palabras). El resultado se
        # guarda en self._quiere_review y se reutiliza al final del turno, de
        # modo que Joker NO necesita invocar a BigBoss: el sistema lo hace solo.
        self._quiere_review = False
        if (not self.confirm) and not self.has_pending(chat_id) and not self.sin_bigboss:
            _prev = getattr(self, "_ultima_edicion", None)
            # lilJoker NO corre en cada turno: solo interpreta tu respuesta cuando hay
            # una edición previa de Joker aún sin revisar que BigBoss pudiera auditar
            # (rol real de lilJoker). Si no existe tal edición no hay nada que
            # interpretar → no se gasta la llamada (0 tokens de lilJoker ese turno).
            if _prev and _prev.get("paths") and not _prev.get("revisada"):
                self._quiere_review = self._quiere_bigboss(user_query).get("quiere_review", False)
                if self._quiere_review:
                    _r, _m = self.review_solution(
                        _prev.get("tarea") or user_query,
                        "Lo ultimo que hizo Joker (resumen suyo; NO es autoridad):\n"
                        + str(_prev.get("propuesta") or "")[:2500],
                        list(_prev["paths"]))
                    self._spend_merge("bigboss", _m)
                    if _m:
                        self._ultima_edicion["revisada"] = True
                        self.brain.remember(_r, kind="assistant")
                    return ("👑 BigBoss reviso el ultimo cambio de Joker:\n\n"
                            + _r).strip(), self._stats_from_roles()

        # ── ¿responde a una edición que estaba en pausa? (intérprete IA, cualquier idioma) ──
        if self.confirm and self.has_pending(chat_id):
            rec = self._pending.get(chat_id)
            cls = self._classify_confirmation(user_query, rec)
            act = cls.get("action", "new_task")
            nota = cls.get("short", "")
            if act in ("apply_with_review", "apply_direct"):
                # Si ya fue revisado por BigBoss (REVISAR) y el humano ahora acepta o
                # fuerza aplicar ("aplica igual"), NO volvemos a revisar: aplicamos.
                if rec.get("revisado"):
                    return self._apply_pending(chat_id, with_bigboss=False)
                return self._apply_pending(
                    chat_id,
                    with_bigboss=(act == "apply_with_review" and not self.sin_bigboss))
            if act == "cancel":
                self._drop_pending(chat_id)
                return (nota or "Entendido: cancelé la edición pendiente (no se escribió nada)."), \
                    _mk_stats()
            # revise / new_task / fallback → no se aplica nada; se descarta la pendiente
            # y este mensaje se trata como una instrucción nueva (turno normal).
            self._drop_pending(chat_id)


        static_sys, dynamic_ctx = context_mod.build_prompts(
            self.brain, self.persona_path, self.name, self.workspace,
            user_query, budget_tokens=self.budget)
        system = static_sys
        if getattr(self, "_quiere_review", False):
            system += ("\n\n[AVISO DEL SISTEMA] El humano pidió que BigBoss revise al final "
                       "de este turno. El sistema lo invocará automáticamente cuando termines; "
                       "NO intentes llamar a BigBoss con tus herramientas ni leas run.py "
                       "buscando cómo activarlo. Haz tu trabajo y cierra con ```final.")

        # ── métricas del turno (estilo tu docker: tokens/gasto/herramientas) ──
        agg = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
               "cache_hit": 0, "cost": 0.0, "calls": 0}
        tools_used = []
        mem_sent_tokens = len(system) // 4

        def _acc(meta):
            u = meta["usage"]
            agg["prompt_tokens"] += u["prompt_tokens"]
            agg["completion_tokens"] += u["completion_tokens"]
            agg["total_tokens"] += u["total_tokens"]
            agg["cache_hit"] += u["prompt_cache_hit_tokens"]
            agg["cost"] += meta["cost"]
            agg["calls"] += 1
            self._spend_add("joker", meta)

        msgs = [{"role": "system", "content": system}]
        # CONTEXTO dinámico (memoria+fecha) como mensaje de usuario: así el prefijo
        # de SISTEMA (STATIC) queda estable entre turnos y DeepSeek lo CACHEA.
        if (dynamic_ctx or "").strip():
            msgs.append({"role": "user", "content": "[CONTEXTO/MEMORIA]\n" + dynamic_ctx})
        # historial reciente en memoria (continúa entre sesiones).
        # IMPORTANTE: recortamos cada item y limitamos cuántos re-inyectamos, para
        # que el modelo NO se inunde con su propio texto repetido y se ponga a repetir.
        for item in self.brain.recall(user_query, budget_tokens=300,
                                      include_recent=3)["working"]:
            role = "user" if item.get("kind") == "user" else "assistant"
            txt = (item["text"] or "").strip()
            if len(txt) > 300:
                txt = txt[:300] + "…"
            msgs.append({"role": role, "content": txt})
        msgs.append({"role": "user", "content": user_query})

        # ── Tool calling NATIVO (function calling de la API) ──
        # Arreglo de RAÍZ: en vez del frágil protocolo de texto, pasamos las
        # funciones a la API y leemos tool_calls ESTRUCTURADOS. Así el modelo no
        # "se escapa" a su formato de texto nativo (XML/DSML). _ask centraliza.
        _native = str(cfg("TW_NATIVE_TOOLS", "1")).lower() in ("1", "yes", "on", "true")
        TOOLS = skills.tools_spec() if _native else None

        def _ask(m, max_tokens=None):
            r, mt = llm.chat_verbose(m, max_tokens=max_tokens or self.max_out,
                                     tools=TOOLS, tool_choice=("auto" if TOOLS else None))
            _acc(mt)
            # telemetría POR LLAMADA (para ver la curva de contexto y el gasto real)
            try:
                _u = mt["usage"]
                self.log.write("call", {
                    "in": _u["prompt_tokens"], "out": _u["completion_tokens"],
                    "cache": _u.get("prompt_cache_hit_tokens", 0),
                    "msgs": len(m),
                    "chars": sum(len(str(x.get("content") or "")) for x in m),
                    "tcalls": len(mt.get("tool_calls") or []),
                    "cost": round(mt.get("cost", 0.0), 6)})
            except Exception:  # noqa: BLE001
                pass
            return r, mt

        # ── FASE 0 · ENTENDER + PLANEAR (garantizado por CÓDIGO) ──
        # En vez de lanzarlo a usar herramientas, hacemos UNA llamada SIN tools para
        # que PRIMERO entienda y diga su plan. Luego se habilita la ejecución.
        plan_txt = ""
        _pq = (user_query or "").strip()
        if (str(cfg("TW_PLAN_FIRST", "1")).lower() in ("1", "yes", "on", "true")
                and len(_pq) >= cfg_int("TW_PLAN_MIN_CHARS", 20)):
            try:
                _pm = msgs + [{"role": "user", "content":
                               "(sistema) NO ejecutes nada todavía y no uses herramientas. "
                               "Responde SOLO: (1) qué te piden (1 línea) y (2) tu plan en "
                               "2-4 pasos concretos. Breve."}]
                plan_txt, _pmeta = llm.chat_verbose(
                    _pm, max_tokens=cfg_int("TW_PLAN_MAX_TOKENS", 320))
                _acc(_pmeta)
                plan_txt = (plan_txt or "").strip()
                if plan_txt:
                    self.log.write("plan", {"text": plan_txt[:800]})
                    msgs.append({"role": "assistant", "content": plan_txt})
                    msgs.append({"role": "user", "content":
                                 "(sistema) Ahora EJECUTA ese plan con tus herramientas. Si de "
                                 "verdad no hace falta ninguna, responde directamente."})
            except Exception as e:  # noqa: BLE001
                self.log.write("error", {"plan_first": str(e)})

        reply, meta = _ask(msgs)
        fix_attempts = 0
        seen_cmds = set()      # guard: no re-ejecutar el mismo comando a ciegas
        acted = False          # ¿ejecutó alguna herramienta en este turno?
        edited = False         # ¿escribió/modificó archivos (dispara el revisor BigBoss)?
        edited_paths = []      # rutas tocadas en este turno (evidencia fría para BigBoss)
        staged = []            # pausa real: write/append guardados hasta tu sí/no
        keep_going = 0         # nº de veces que forzamos a cerrar con ```final
        for _ in range(max_iters):
            # 1) tool-calls NATIVOS de la API (camino principal y robusto)
            native = meta.get("tool_calls") or []
            calls = []
            for _tc in native:
                _fn = (_tc.get("function") or {})
                _nm = (_fn.get("name") or "").strip()
                try:
                    _ar = json.loads(_fn.get("arguments") or "{}")
                except Exception:
                    _ar = {}
                calls.append((_nm, _ar if isinstance(_ar, dict) else {}, _tc.get("id")))
            native_mode = bool(calls)
            # 2) Fallback: protocolo de texto (```tool / ```file / XML / DSML nativo)
            if not calls:
                calls = [(n, a, None) for n, a in skills.parse_tool_calls(reply)]
                if calls:
                    self.log.write("aviso", {"tipo": "text_tool_fallback",
                                             "detalle": "tool-call por protocolo de texto",
                                             "n": len(calls)})
            if not calls:
                # Marcado de herramienta que NO se pudo interpretar: lo decimos y
                # pedimos reemitir (nunca se ejecuta ni se muestra al humano).
                if skills.has_raw_tool_markup(reply) and fix_attempts < 3:
                    fix_attempts += 1
                    self.log.write("aviso", {"tipo": "tool_markup_no_reconocido",
                                             "preview": reply[:300]})
                    msgs.append({"role": "assistant", "content": reply})
                    msgs.append({"role": "user",
                                 "content": "No pude interpretar tu llamada a herramienta, así que "
                                            "NO se ejecutó. Vuelve a llamar a la función correcta "
                                            "(tools); NO escribas XML/DSML ni bloques de herramienta "
                                            "en el texto."})
                    reply, meta = _ask(msgs)
                    continue
                # Si el agente empezó un bloque ```tool pero quedó SIN CERRAR (p. ej.
                # cortado por límite de salida), avísale para que:
                #  1) reconozca el fallo al humano, y
                #  2) NO reintente el bloque gigante: escriba el archivo EN PARTES
                #     (write_file con la 1ª parte y append_file con el resto).
                openers = ("```tool" in reply) or ("```file" in reply) or ("```append" in reply)
                if openers and reply.count("```") % 2 == 1 and fix_attempts < 3:
                    fix_attempts += 1
                    msgs.append({"role": "assistant", "content": reply})
                    msgs.append({"role": "user",
                                 "content": "Tu bloque de herramienta quedó CORTADO o sin cerrar "
                                            "(falta el cierre ```), así que esa llamada FALLÓ y no se "
                                            "ejecutó. En tu MENSAJE FINAL al humano debes mencionar "
                                            "brevemente este error (qué falló y cómo lo resolviste). "
                                            "Ahora NO reintentes todo el archivo en un solo write_file "
                                            "(volverá a cortarse): repártelo en 2-3 partes. Usa write_file "
                                            "con la primera parte y luego append_file con el resto, una "
                                            "llamada pequeña por cada mensaje. Empieza por la primera parte."})
                    reply, meta = llm.chat_verbose(msgs, max_tokens=self.max_out)
                    _acc(meta)
                    continue
                # ── FINAL robusto e independiente del idioma ──
                # Solo se cierra el turno si el agente marcó su mensaje final con
                # ```final. Si trabajó con tools y termina sin marcarlo, le pedimos
                # completar o cerrar con el marcador (bounded).
                if FINAL_MARK in reply:
                    reply = reply.split(FINAL_MARK)[0].rstrip()
                    break
                if acted and keep_going < 4:
                    keep_going += 1
                    msgs.append({"role": "assistant", "content": reply})
                    msg = ("Termina la tarea EN ESTE MISMO turno: si te queda algo por hacer "
                           "(leer, editar, verificar), ejecútalo AHORA con tus tools; los "
                           "archivos grandes escríbelos por partes en este turno (write_file "
                           "primera parte + append_file el resto). NO narres planes ni "
                           "preguntes '¿continúo?'. Cuando termines, da tu respuesta final y "
                           "cierra con una línea exacta: ```final")
                    if keep_going >= 3:
                        msg = ("⚠️ Todavía no cerraste tu respuesta final. Ejecuta AHORA lo que "
                               "falte con tus tools y termina ya; no vuelvas a describir el "
                               "plan, hazlo. Cierra con: ```final")
                    msgs.append({"role": "user", "content": msg})
                    reply, meta = llm.chat_verbose(msgs, max_tokens=self.max_out)
                    _acc(meta)
                    continue
                # Última pasada forzada: si trabajó y aún no cerró con ```final, un intento
                # más para que ejecute lo pendiente o entregue el resultado final.
                if acted and FINAL_MARK not in reply:
                    msgs.append({"role": "assistant", "content": reply})
                    msgs.append({"role": "user",
                                 "content": "Cierra YA. O ejecuta con tus tools lo que falte "
                                            "ahora mismo, o si no queda más trabajo responde al "
                                            "humano con el resultado final y cierra con ```final."})
                    reply, meta = llm.chat_verbose(msgs, max_tokens=self.max_out)
                    _acc(meta)
                break
            for name, _args, _tid in calls:
                if name and name not in tools_used:
                    tools_used.append(name)
            if native_mode:
                # mensaje del asistente con sus tool_calls (formato OpenAI), pero
                # SIN el contenido completo de los archivos escritos (stub) para no
                # reenviarlo en cada iteración: el archivo ya está en disco.
                msgs.append({"role": "assistant", "content": reply or "",
                             "tool_calls": _stub_native_calls(native)})
            else:
                msgs.append({"role": "assistant",
                             "content": skills.stub_file_blocks(reply)})
            results = []
            tool_msgs = []          # respuestas role=tool (modo nativo)
            pause_hit = False
            for name, args, _tid in calls:
                # GUARD estructural: si el mismo comando run_shell ya se ejecutó en
                # esta vuelta, NO lo re-ejecutamos a ciegas. Se le indica al agente que
                # lea el código de la función responsable antes de volver a correr.
                if name == "run_shell":
                    cmd = str((args or {}).get("command", "")).strip()
                    if cmd and cmd in seen_cmds:
                        _g = (f"[skill run_shell] ⚠️ GUARD: este comando ya se ejecutó en esta "
                              f"vuelta y no lo repito a ciegas. Antes de volver a correr, LEE el "
                              f"código de la función responsable (read_file con start/end). Causas "
                              f"típicas de salida vacía: max_tokens bajo / finish_reason='length' "
                              f"(el 'thinking' gastó el presupuesto). Arregla el código y solo "
                              f"entonces verifica.")
                        results.append(_g)
                        if _tid is not None:
                            tool_msgs.append({"role": "tool", "tool_call_id": _tid, "content": _g})
                        continue
                    seen_cmds.add(cmd)
                _is_write = name in ("write_file", "append_file")
                if self.confirm and _is_write:
                    # PAUSA REAL: no escribimos aún; guardamos y pedimos confirmación.
                    _p = (args.get("path") or "").strip()
                    staged.append({"op": name, "path": _p,
                                   "content": args.get("content", "")})
                    if _p and _p not in edited_paths:
                        edited_paths.append(_p)
                    out = (f"[EDICIÓN EN PAUSA] No escribí todavía. Guardé la modificación "
                           f"de '{_p}' ({name}). Detente de editar. Presenta tu plan al humano "
                           f"y pregúntale: ¿Quieres que BigBoss verifique el cambio antes de "
                           f"aplicarlo? (responde 'sí' o 'no').")
                    acted = True
                    pause_hit = True
                    self.log.write("tool", {"name": name, "args": _safe_args(name, args),
                                            "preview": "PAUSA_CONFIRMACION"})
                    if self.tool_hook:
                        self.tool_hook(name, args, "en pausa (confirmación del humano)")
                    results.append(f"[skill {name} → PAUSA confirmación] {out}")
                    if _tid is not None:
                        tool_msgs.append({"role": "tool", "tool_call_id": _tid, "content": out})
                    else:
                        msgs.append({"role": "user", "content": out})
                    continue
                if self.tool_hook:
                    self.tool_hook(name, args, None)      # estado "ejecutando…"
                out = skills.execute_tool_call(name, args, self._deps())
                acted = True   # se ejecutó una herramienta (no es un simple chat)
                if _is_write:
                    edited = True   # editó código -> BigBoss deberá revisar
                    _p = (args.get("path") or "").strip()
                    if _p and _p not in edited_paths:
                        edited_paths.append(_p)
                self.log.write("tool", {"name": name, "args": _safe_args(name, args),
                                        "preview": (out or "")[:200]})
                if self.tool_hook:
                    self.tool_hook(name, args, out)        # estado final "… ✓"
                results.append(f"[skill {name}] {_trim_tool(out)}")
                if _tid is not None:
                    tool_msgs.append({"role": "tool", "tool_call_id": _tid,
                                      "content": str(out)})
            if native_mode:
                msgs.extend(tool_msgs)
            else:
                msgs.append({"role": "user",
                             "content": "Resultados de las tools:\n" + "\n".join(results)
                                        + "\nContinúa o responde al humano."})
            if pause_hit:
                msgs.append({"role": "user",
                             "content": "NO ejecutes más herramientas de edición. Termina tu "
                                        "mensaje presentando el plan y la pregunta al humano, "
                                        "en SU MISMO IDIOMA, y cierra con ```final."})
            _podar_historial(msgs)   # acota el contexto que se reenvía
            reply, meta = _ask(msgs)

        # ── GUARDIA FINAL: si quedó un bloque de herramienta CRUDO sin ejecutar,
        #    nunca lo mostramos al humano. Hacemos UNA llamada para que dé un
        #    resumen claro en texto (qué hizo / qué falta / continúa).
        if skills.has_raw_tool_markup(reply):
            self.log.write("aviso", {"tipo": "tool_markup_crudo",
                                     "detalle": "quedó marcado de herramienta sin ejecutar "
                                                "(XML <tool_calls>/<invoke> o fence crudo); "
                                                "no se mostró al humano",
                                     "preview": reply[:300]})
            msgs.append({"role": "assistant", "content": reply})
            msgs.append({"role": "user",
                         "content": "⚠️ Ninguna de esas herramientas se ejecutó y tu mensaje tiene "
                                    "bloques de herramienta en bruto (XML o ```tool). NO los escribas "
                                    "en tu respuesta final. Responde en TEXTO PLANO y claro: 1) qué "
                                    "HICISTE, 2) qué te FALTA por hacer, 3) continúa con el siguiente "
                                    "paso (o di que te detuviste)."})
            reply, meta = llm.chat_verbose(msgs, max_tokens=4000)
            _acc(meta)
        # ── Si el turno se quedó SIN respuesta de texto (p. ej. agotó las
        #    iteraciones en medio de tool-calls, o la última respuesta fue solo un
        #    tool_call), forzamos un resumen final EN TEXTO (sin herramientas). ──
        if not (reply or "").strip():
            self.log.write("aviso", {"tipo": "reply_vacio_forzar_resumen"})
            msgs.append({"role": "user",
                         "content": "Dame AHORA tu respuesta final al humano, en TEXTO PLANO y en "
                                    "su mismo idioma: 1) qué HICISTE, 2) qué te FALTA, 3) el "
                                    "siguiente paso. No llames a más herramientas."})
            try:
                reply, meta = llm.chat_verbose(msgs, max_tokens=800)
                _acc(meta)
            except Exception as e:  # noqa: BLE001
                self.log.write("error", {"resumen_final": str(e)})
        # por si acaso quedara algún bloque crudo, lo quitamos del texto visible.
        _clean = skills.strip_tool_markup(reply)
        if _clean:
            reply = _clean
        elif skills.has_raw_tool_markup(reply):
            reply = "⚠️ No pude ejecutar la acción solicitada. ¿Me lo repites o lo detallo?"
        elif not (reply or "").strip():
            reply = ("⚠️ Terminé el turno sin cerrar una respuesta (posible bucle de herramientas). "
                     "Dime de nuevo el foco y lo cierro.")
        else:
            reply = reply.strip()

        # ── Mostrar el PLAN al humano (Fase 0): así ve que entendió ANTES de ejecutar ──
        if (plan_txt and str(cfg("TW_SHOW_PLAN", "1")).lower() in ("1", "yes", "on", "true")
                and plan_txt not in reply):
            reply = f"🧭 Plan:\n{plan_txt}\n\n———\n{reply}"

        # ── PAUSA REAL: si hay ediciones staged y estamos en modo confirmación,
        #    NO se ha escrito nada aún → lo guardamos como pendiente y preguntamos.
        if self.confirm and staged and not edited:
            self._pending[chat_id] = {"task": user_query, "staged": staged,
                                      "plan": reply.strip()[:1500], "ts": time.time()}
            self._save_pending()
            if "?" not in reply[-160:]:
                # la pregunta la redacta la PROPIA IA, en el idioma del humano (sin texto fijo)
                msgs.append({"role": "assistant", "content": reply})
                _aviso = ("(Resumen del sistema): tienes cambios preparados pero NO aplicados "
                          "todavía. Termina tu mensaje preguntando al humano, EN EL MISMO IDIOMA "
                          "en que te habla, de forma breve, si puedo aplicarlos. No ejecutes "
                          "herramientas.") if self.sin_bigboss else (
                          "(Resumen del sistema): tienes cambios preparados pero NO aplicados "
                          "todavía. Termina tu mensaje preguntando al humano, EN EL MISMO IDIOMA "
                          "en que te habla, de forma breve, si quiere una segunda opinión "
                          "(BigBoss) antes de aplicar. No ejecutes herramientas.")
                msgs.append({"role": "user", "content": _aviso})
                reply, meta = llm.chat_verbose(msgs, max_tokens=self.max_out)
                _acc(meta)
            edited = False  # nada escrito: no hay nada que el revisor verifique aún

        # BigBoss al final del turno SOLO si liljoker ya detecto (arriba) que el
        # humano lo pidio y Joker edito archivos en este turno. Se reutiliza la
        # clasificacion de arriba: no se llama a liljoker dos veces y nunca corre
        # BigBoss de forma automatica.
        review_meta = None
        if edited:
            self._ultima_edicion = {"paths": list(edited_paths),
                                    "tarea": user_query,
                                    "propuesta": reply}
            if getattr(self, "_quiere_review", False) and not self.sin_bigboss:
                reply, review_meta = self.review_solution(user_query, reply, edited_paths)
                if review_meta:
                    self._ultima_edicion["revisada"] = True

        # gasto por rol: BigBoss (revisión) se suma como su propio bucket
        if review_meta:
            self._spend_merge("bigboss", review_meta)

        # guardar lo vivido (memoria episódica: chunks con vector)
        self.brain.remember(user_query, kind="user")
        self.brain.remember(reply, kind="assistant")

        stats = {
            "usage": agg, "tools_used": tools_used,
            "model": meta.get("model", "?"),
            "mem_sent_tokens": mem_sent_tokens,
            "budget_tokens": self.budget,
            "review": review_meta,
            "roles": dict(self._spend),   # joker / bigboss / liljoker — por rol
        }
        self.log.write("turn", {"user": user_query[:300],
                                "tools": tools_used,
                                "in": agg["prompt_tokens"],
                                "out": agg["completion_tokens"],
                                "cost": round(agg["cost"], 6)})
        self.log.write("reply", {"agent": self.name, "text": reply.strip()[:2000]})
        return reply.strip(), stats

    # ── aprender de verdad: consolidación bruto → neto ─────────────────────
    def consolidate_now(self) -> dict:
        def _chat_fn(msgs):
            # max_tokens ALTO: con deepseek-v4-flash + thinking, max_tokens bajos hacen
            # que el modelo gaste en razonar y devuelva content VACÍO (no aprendería nada).
            return llm.chat(msgs, max_tokens=8192, temperature=0.2)
        res = consol_mod.consolidate(
            self.brain, _chat_fn, agent_name=self.name,
            persona_hint="asistir al humano con memoria persistente",
            min_chunks=cfg_int("TW_CONSOLIDATE_MIN_CHUNKS", 2))
        self.log.write("consolidate", res)
        return res

    def review_solution(self, user_query: str, proposal: str, edited_paths: list = None):
        """FILTRO BigBoss COLD (2º LLM crítico) cuando Joker editó.

        - Recibe DATOS CRUDOS (archivos reales que Joker tocó), NO su narrativa.
        - Puede pedir CONTEXTO bajo demanda (el sistema, no Joker, le lee el archivo).
        - Anti-bucle: máx. TW_REVIEW_MAX_ROUNDS rondas; sin consenso -> reporte NETO al humano.
        Devuelve (propuesta_final, meta_review | None)."""
        if str(cfg("TW_ENABLE_REVIEW", "1")).lower() not in ("1", "yes", "on", "true"):
            return proposal, None
        model = cfg("TW_REVIEW_MODEL", "") or "deepseek-v4-pro"
        base_url = cfg("TW_REVIEW_BASE_URL", "") or "https://api.deepseek.com/v1/chat/completions"
        api_key = cfg("TW_REVIEW_API_KEY", "")
        max_rounds = cfg_int("TW_REVIEW_MAX_ROUNDS", 2)
        rev_max_tokens = cfg_int("TW_REVIEW_MAX_TOKENS", 12000)
        max_fetches = cfg_int("TW_REVIEW_MAX_FETCHES", 3)
        is_kimi = "moonshot.ai" in base_url

        meta = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "cost": 0.0, "seg": 0.0, "rounds": 0, "model": model,
                "criticas": [], "contextos_leidos": []}

        def _acc(m):
            u = m["usage"]
            meta["calls"] += 1
            meta["prompt_tokens"] += u["prompt_tokens"]
            meta["completion_tokens"] += u["completion_tokens"]
            meta["cost"] += m["cost"]
            meta["seg"] += m.get("elapsed", 0.0) or 0.0

        def _bb(messages):
            return llm.chat_verbose(
                messages, temperature=0.2, max_tokens=rev_max_tokens,
                model=model, base_url=base_url, api_key=api_key,
                price_provider="kimi" if is_kimi else None)

        # ── contexto COLD inicial: tarea + contenido REAL de los archivos tocados ──
        datos = [f"TAREA del usuario:\n{user_query[:3000]}"]
        if edited_paths:
            datos.append("\nARCHIVOS que Joker tocó en este turno (contenido REAL):")
            for p in edited_paths:
                meta["contextos_leidos"].append(p)
                datos.append(f"\n--- {p} ---\n{_read_for_review(self.workspace, p)}")
        datos.append("\n(El resumen de Joker NO es autoridad; verifícalo contra esos archivos.)")
        datos = "\n".join(datos)

        def _evaluate(joker_claim: str) -> str:
            """BigBoss evalúa. Puede pedir archivos bajo demanda (el sistema los lee)."""
            msgs = [{"role": "system", "content": SYSTEM_REVIEWER},
                    {"role": "user",
                     "content": datos + "\n\nLo que Joker afirma/propone:\n" + joker_claim[:2500]}]
            for _ in range(max_fetches):
                v, m = _bb(msgs)
                _acc(m)
                need = _REVIEW_CTX_RE.findall(v)
                if need and len(v.strip()) <= 120:   # pedido puro de contexto
                    p = need[0]
                    meta["contextos_leidos"].append(p)
                    msgs.append({"role": "assistant", "content": v})
                    msgs.append({"role": "user",
                                 "content": f"[BigBoss pidió contexto] {p}:\n"
                                            f"{_read_for_review(self.workspace, p)}\n\n"
                                            f"Ahora evalúa y da tu VEREDICTO final."})
                    continue
                return v
            # agotó contexto bajo demanda: forzar veredicto
            msgs.append({"role": "user", "content": "No hay más contexto. Da tu VEREDICTO final."})
            v, m = _bb(msgs)
            _acc(m)
            return v

        def _joker_redo(cur: str, critica: str) -> str:
            c2, m2 = llm.chat_verbose(
                [{"role": "system",
                  "content": "Eres Joker, ingeniero. Rehaz tu solución de forma ROBUSTA "
                             "atendiendo la crítica de BigBoss (causa raíz, sin parches, "
                             "cubre efectos)."},
                 {"role": "user",
                  "content": f"Tu propuesta anterior:\n{cur}\n\nCrítica de BigBoss:\n"
                             f"{critica[:2500]}\n\nReescribe la solución robusta (breve)."}],
                temperature=0.3, max_tokens=self.max_out)
            _acc(m2)
            return (c2 or "").strip() or cur

        # ── rondas de veredicto (anti-bucle: máx. max_rounds) ──
        cur, ok = proposal, False
        for r in range(max_rounds + 1):
            rev_txt = _evaluate(cur)
            meta["rounds"] = r + 1
            if "REVISAR" in rev_txt.upper():
                meta["criticas"].append(rev_txt)      # BRUTO completo para auditoría
                if r >= max_rounds:
                    break
                cur = _joker_redo(cur, rev_txt)       # Joker rehace atendiendo a BigBoss
            else:
                ok = True
                break

        if not ok and meta["criticas"]:
            meta["sin_consenso"] = True
            # NETO para el usuario (frase completa, con la recomendación si la dio)
            cur += ("\n\n🤝 [BigBoss] Tras %d ronda(s) no hay consenso. Su opinión: %s\n"
                    "Razonamiento completo en auditoría. ¿Cómo lo resolvemos?"
                    % (meta["rounds"], _bb_neto(meta["criticas"][-1])))
        return cur, meta

    def net_state(self) -> dict:
        """Estado 'neto' de la memoria: qué sabe y qué ha aprendido de verdad."""
        beliefs = self.brain.beliefs(limit=100)
        lessons = self.brain.lessons(limit=100)
        rows = self.brain._conn.execute(
            "SELECT count(*) AS n FROM chunks WHERE agent=? AND kind='summary'",
            (self.store_id,)).fetchone()
        pend = self.brain._conn.execute(
            "SELECT count(*) AS n FROM chunks WHERE agent=? "
            "AND (processed IS NULL OR processed=0)",
            (self.store_id,)).fetchone()
        return {
            "beliefs": beliefs,
            "lessons_win": sum(1 for l in lessons if l["kind"] == "win"),
            "lessons_fail": sum(1 for l in lessons if l["kind"] == "fail"),
            "summaries": (rows["n"] if rows else 0),
            "pending_bruto": (pend["n"] if pend else 0),
        }

    def close(self):
        self.brain.close()


_ROLE_ICON = {"joker": "🃏", "bigboss": "👑", "liljoker": "🤏", "joker_redo": "🔁"}


def _pide_bigboss(texto) -> bool:
    """True SOLO si el humano pide explicitamente que BigBoss revise.
    No se activa con 'revisa los logs' ni 'revisa' a secas."""
    t = (texto or "").lower()
    return any(k in t for k in (
        "bigboss", "big boss", "segunda opinion", "segunda opinión",
        "que revise", "verificalo", "verifícalo"))


def _role_line(name: str, s: dict) -> str:
    seg = s.get("seg", 0.0) or 0.0
    return (f"{_ROLE_ICON.get(name, '⚙️')} {name} ({s.get('model', '?')}) · "
            f"📚 {s['calls']} · ⬆️ in {s['prompt_tokens']:,} · "
            f"⬇️ out {s['completion_tokens']:,} · ⏱ {seg:.1f}s · 💰 ${s['cost']:.5f}")


def format_summary(stats: dict, as_footer: bool = False) -> str:
    """Resumen del turno. Si hay stats['roles'] muestra CADA rol (joker/bigboss/
    liljoker) con sus tokens y coste individuales + un TOTAL. Si no, usa la vista
    clásica de una línea."""
    tools_s = (" · ".join(f"{_emoji_tool(t)} {t}" for t in stats["tools_used"])
               if stats.get("tools_used") else "ninguna")
    roles = stats.get("roles") or {}
    if roles:
        blocks = []
        if as_footer or stats.get("mem_sent_tokens"):
            pct = stats["mem_sent_tokens"] / max(stats.get("budget_tokens", 1), 1)
            blocks.append(f"🔧 herramientas: {tools_s} · 🧠 memoria "
                          f"{stats['mem_sent_tokens']}/{stats.get('budget_tokens', 0)}t "
                          f"({pct * 100:.0f}%)")
        order = ("joker", "bigboss", "liljoker")
        shown = set()
        for name in order + tuple(k for k in roles if k not in order):
            s = roles.get(name)
            if not s or not s.get("calls"):
                continue
            blocks.append(_role_line(name, s))
            shown.add(name)
        tot = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
               "total_tokens": 0, "cache_hit": 0, "cost": 0.0, "seg": 0.0}
        for name, s in roles.items():
            if not s or not s.get("calls"):
                continue
            tot["calls"] += s.get("calls", 0)
            tot["prompt_tokens"] += s.get("prompt_tokens", 0)
            tot["completion_tokens"] += s.get("completion_tokens", 0)
            tot["total_tokens"] += s.get("total_tokens",
                                         s.get("prompt_tokens", 0) + s.get("completion_tokens", 0))
            tot["cache_hit"] += s.get("cache_hit", 0)
            tot["cost"] += s.get("cost", 0.0)
            tot["seg"] += s.get("seg", 0.0) or 0.0
        tc = f"Σ TOTAL · 📚 {tot['calls']} · ⬆️ in {tot['prompt_tokens']:,} · " \
             f"⬇️ out {tot['completion_tokens']:,} · ⏱ {tot['seg']:.1f}s ·"
        if tot["cache_hit"]:
            tc += f" 📀 caché {tot['cache_hit']:,} ·"
        tc += f" 💰 ${tot['cost']:.5f}"
        blocks.append(tc)
        line = "\n".join(blocks)
    else:
        u = stats["usage"]
        parts = [f"🃏 {stats['model']}", f"📚 llamadas: {u['calls']}",
                 f"⬆️ in {u['prompt_tokens']:,}", f"⬇️ out {u['completion_tokens']:,}"]
        if u["cache_hit"]:
            parts.append(f"📀 caché {u['cache_hit']:,}")
        parts.append(f"🔧 herramientas: {tools_s}")
        parts.append(f"💰 ${u['cost']:.5f}")
        line = " · ".join(parts)
    if as_footer:
        return "\n\n---\n" + line
    return line


def run_oneshot(query: str):
    agent = Agent()
    print(f"🧠 {agent.name} (embeddings reales: {embeddings.using_real_embeddings()})")
    print("— memoria recuperando —")
    try:
        out, stats = agent.run_turn(query)
    except Exception as e:
        print(f"⚠️ error: {e}")
        agent.close()
        return
    print(out + format_summary(stats, as_footer=True))
    agent.close()



def run_daemon():
    token = cfg("TW_TELEGRAM_BOT_TOKEN")
    if not token:
        # Sin token de Telegram el agente NO tiene por qué morir: la Superconsola
        # web es una vía de uso completa por sí sola. Antes salía con error y, con
        # `restart: unless-stopped` en Docker, entraba en un BUCLE infinito de
        # reinicios (quemaba CPU y llenaba los logs) si el usuario no había puesto
        # token todavía. Ahora se queda vivo sirviendo la consola y explicándolo.
        print("=" * 70, flush=True)
        print("⚠️  No hay TW_TELEGRAM_BOT_TOKEN en agent/.env", flush=True)
        print("    El agente funcionará SOLO por la Superconsola web (sin Telegram).", flush=True)
        print("    Para hablarle por Telegram:", flush=True)
        print("      1. Crea un bot con @BotFather (/newbot)", flush=True)
        print("      2. Copia el token en agent/.env -> TW_TELEGRAM_BOT_TOKEN=...", flush=True)
        print("      3. Reinicia el agente (docker compose ... restart)", flush=True)
        print("    Para una prueba rápida sin Telegram:", flush=True)
        print('      python3 run.py --oneshot "hola, preséntate"', flush=True)
        print("=" * 70, flush=True)
    agent = Agent()
    if token:
        print(f"🧠 {agent.name} escuchando Telegram… (Ctrl+C para salir)")
    else:
        print(f"🧠 {agent.name} activo SIN Telegram (solo consola web / --oneshot)."
              " Ctrl+C para salir.")

    # API LOCAL de turnos para la WEB: el MISMO agente (mismas herramientas y
    # memoria, mismo proceso). Solo se levanta si el agente tiene 'http_port'.
    _pw = str(cfg("TW_HTTP_PORT", "") or "").strip()
    if _pw.isdigit():
        threading.Thread(target=_servidor_turno, args=(agent, int(_pw)),
                         daemon=True).start()
    else:
        print("[http] (sin 'http_port' en la config: la web usará el chat LLM)")

    global tbot

    def on_message(chat_id, text):
        tbot.send_typing(chat_id)
        low = text.strip().lower()
        if low.startswith("/win") or low.startswith("/ok"):
            body = text[4:].strip() or "acción validada"
            agent.brain.learn("win", "Validado por el humano", body)
            tbot.send(chat_id, "✅ Anotado como victoria. Aprendo de ello.")
            return
        if low.startswith("/fail") or low.startswith("/mal"):
            body = text[5:].strip() or "acción reportada como fallida"
            agent.brain.learn("fail", "Reportado por el humano", body)
            tbot.send(chat_id, "❌ Anotado como fallo. No lo repetiré.")
            return
        if low in ("/memoria", "/recall", "/estado"):
            ns = agent.net_state()
            beliefs = "\n".join(f"· {b['key']} = {b['value']}"
                                for b in ns["beliefs"]) or "(ninguna)"
            head = (f"🧠 NETO — creencias: {len(ns['beliefs'])}, "
                    f"victorias: {ns['lessons_win']}, fallos: {ns['lessons_fail']}, "
                    f"resúmenes: {ns['summaries']}, bruto pendiente: {ns['pending_bruto']}")
            tbot.send(chat_id, head + "\n\n" + beliefs)
            return
        if low == "/neto":
            ns = agent.net_state()
            tbot.send(chat_id,
                      f"🧠 NETO — creencias {len(ns['beliefs'])} · "
                      f"✅ victorias {ns['lessons_win']} · ❌ fallos {ns['lessons_fail']} · "
                      f"📚 resúmenes {ns['summaries']} · bruto sin consolidar {ns['pending_bruto']}")
            return
        if low in ("/pendiente", "/pendientes", "/pending"):
            if agent.has_pending(str(chat_id)):
                rec = agent._pending.get(str(chat_id), {})
                n = len(rec.get("staged", []))
                rutas = ", ".join(s.get("path", "?") for s in rec.get("staged", []))
                tbot.send(chat_id, f"⏸ Hay una edición en pausa ({n} operación/es: {rutas}).\n"
                                   f"Responde: 'sí' (BigBoss), 'no' (directo) o 'cancelar'.")
            else:
                tbot.send(chat_id, "No hay ninguna edición pendiente.")
            return
        if low in ("/cancelar", "/descartar"):
            if agent.has_pending(str(chat_id)):
                agent._drop_pending(str(chat_id))
                tbot.send(chat_id, "🗑 Edición pendiente descartada (no se escribió nada).")
            else:
                tbot.send(chat_id, "No había ninguna edición pendiente.")
            return
        if low in ("/consolidar", "/learn"):
            tbot.send(chat_id, "🛌 Consolidando bruto→neto (una llamada LLM, puede tardar)…")
            try:
                with _TURNO_LOCK:
                    res = agent.consolidate_now()
                if res.get("skipped"):
                    tbot.send(chat_id, "Nada que consolidar aún (faltan datos brutos). "
                                       f"Bruto leído: {res.get('raw_read', 0)}.")
                else:
                    tbot.send(chat_id,
                              f"🛌 Consolidación: leídos {res.get('raw_read')} brutos → "
                              f"resumen {bool(res.get('summary_saved'))}, "
                              f"creencias {res.get('beliefs_applied')}, "
                              f"lecciones {res.get('lessons_applied')}.")
            except Exception as e:
                tbot.send(chat_id, f"⚠️ consolidación falló: {e}")
            return
        if low in ("/uso", "/uso del turno", "/stats"):
            s = getattr(agent, "last_stats", None)
            if s:
                tbot.send(chat_id, "Uso del último turno:\n" + format_summary(s))
            else:
                tbot.send(chat_id, "Todavía no hay datos de un turno.")
            return
        # telemetría efímera (estilo OpenClaw): UN mensaje de estado que se
        # actualiza con cada herramienta y se BORRA solo al terminar, sin llenar el chat.
        holder = {"mid": None, "done": []}

        def hook(name, args, result):
            d = _rich_desc(name, args)      # ya trae el EMOJI de la herramienta
            if result is None:
                state = f"{d}…"
            else:
                if name not in holder["done"]:
                    holder["done"].append(name)
                preview = _rich_result(result)
                if preview:
                    state = f"{d}\n\n{preview}"
                else:
                    state = f"{d} ✓"
            if holder["mid"] is None:
                holder["mid"] = tbot.send(chat_id, state)
            else:
                tbot.edit(chat_id, holder["mid"], state)

        agent.tool_hook = hook
        usage_footer = ""
        try:
            with _TURNO_LOCK:   # la WEB y Telegram NUNCA se pisan (un turno cada vez)
                out, stats = agent.run_turn(text, ctx={"chat_id": str(chat_id)})
            agent.last_stats = stats
            # el pie de uso se muestra al final de la respuesta (usage/tools/caché/coste)
            if str(cfg("TW_SHOW_USAGE_FOOTER", "1")).lower() in ("1", "yes", "on", "true"):
                usage_footer = format_summary(stats, as_footer=True)
        except Exception as e:
            out = f"⚠️ error: {e}"
            agent.log.write("error", {"message": str(e),
                                      "trace": traceback.format_exc()[:2000]})
        finally:
            agent.tool_hook = None
            # mensaje efímero: se borra solo al terminar el turno
            if holder["mid"] is not None:
                tbot.delete(chat_id, holder["mid"])
        # respuesta humana + el uso como pie "al final" del MISMO mensaje
        tbot.send(chat_id, out + usage_footer, publicar=True)


    def on_voice(chat_id, file_id):
        """Recibe una nota de voz: transcribe (STT local, 0 tokens), Joker responde
        (razonamiento normal) y devuelve la respuesta por VOZ natural (TTS local)."""
        tdir = os.path.join(HERE, ".audio_tmp")
        os.makedirs(tdir, exist_ok=True)
        oga = os.path.join(tdir, f"v{chat_id}.oga")
        wav = os.path.join(tdir, f"v{chat_id}.wav")
        ogg = os.path.join(tdir, f"v{chat_id}.ogg")
        ok_trans = False
        user_text = ""
        try:
            tbot.send_action(chat_id, "typing")
            fp = tbot.get_file_path(file_id)
            tbot.download_file(fp, oga)
            audio.oga_to_wav(oga, wav)
            user_text = audio.transcribe(wav)
            ok_trans = bool(user_text)
        except Exception as e:  # noqa: BLE001
            agent.log.write("error", {"voice_stt": str(e)})
            tbot.send(chat_id, f"⚠️ No pude procesar tu nota de voz: {e}")
            return
        if not ok_trans:
            tbot.send(chat_id, "🎙️ No entendí el audio (transcripción vacía). Inténtalo más claro.")
            return
        tbot.send(chat_id, f"🎙️ Te escuché: *{user_text}*")

        # ── igual que el texto: telemetría efímera + turno de Joker ──
        holder = {"mid": None, "done": []}
        def hook(name, args, result):
            d = _rich_desc(name, args)      # ya trae el EMOJI de la herramienta
            if result is None:
                state = f"{d}…"
            else:
                prev = _rich_result(result)
                state = f"{d}\n\n{prev}" if prev else f"{d} ✓"
            if holder["mid"] is None:
                holder["mid"] = tbot.send(chat_id, state)
            else:
                tbot.edit(chat_id, holder["mid"], state)
        agent.tool_hook = hook
        usage_footer = ""
        try:
            with _TURNO_LOCK:   # la WEB y Telegram NUNCA se pisan (un turno cada vez)
                out, stats = agent.run_turn(user_text, ctx={"chat_id": str(chat_id)})
            agent.last_stats = stats
            if str(cfg("TW_SHOW_USAGE_FOOTER", "1")).lower() in ("1", "yes", "on", "true"):
                usage_footer = format_summary(stats, as_footer=True)
        except Exception as e:  # noqa: BLE001
            out = f"⚠️ error: {e}"
            agent.log.write("error", {"message": str(e), "trace": traceback.format_exc()[:2000]})
        finally:
            agent.tool_hook = None
            if holder["mid"] is not None:
                tbot.delete(chat_id, holder["mid"])
        tbot.send(chat_id, out + usage_footer, publicar=True)

        # ── respuesta por VOZ (natural, local) ──
        try:
            text_voz = (out or "").strip()
            if text_voz and not text_voz.startswith("⚠️"):
                audio.tts(text_voz[:1500], wav)
                audio.wav_to_ogg(wav, ogg)
                tbot.send_voice(chat_id, ogg)
        except Exception as e:  # noqa: BLE001
            agent.log.write("error", {"voice_tts": str(e)})
            tbot.send(chat_id, f"(no pude generar la voz: {e})")


    # hilo "cron" de consolidación: cada X segundos intenta aprender de verdad
    def consolidator_loop():
        interval = float(cfg("TW_CONSOLIDATE_EVERY", "0"))
        if interval <= 0:
            return
        while True:
            time.sleep(interval)
            try:
                with _TURNO_LOCK:   # no tocar la BD mientras hay un turno (web/Telegram)
                    n = agent.net_state()["pending_bruto"]
                    if n > 0:
                        agent.consolidate_now()
            except Exception as e:
                agent.log.write("consolidate", {"error": str(e)})

    t = threading.Thread(target=consolidator_loop, daemon=True)
    t.start()

    # ── AUTO-ARRANQUE de los demás agentes ──
    # Joker es el PID 1 del contenedor: al reiniciar Docker solo arranca él.
    # Lanzamos (una sola vez) el watcher del supervisor, que levanta y mantiene
    # vivos al resto de agentes (todos MENOS Joker) y a los servicios extra.
    if str(cfg("TW_SUPERVISAR_AGENTES", "1")).lower() in ("1", "yes", "on", "true"):
        try:
            _sp = supervisor.ensure_watcher()
            agent.log.write("supervisor", {"evento": "watcher_asegurado", "pid": _sp})
            # guardián: si el watcher cae, lo relanza (auto-arranque perpetuo)
            _sg = supervisor.ensure_guardian()
            agent.log.write("supervisor", {"evento": "guardian_asegurado", "pid": _sg})
        except Exception as e:  # noqa: BLE001
            agent.log.write("error", {"supervisor": str(e)})

        # ── RE-ASEGURO periódico (último hueco) ──
        # Watcher y guardián se cubren mutuamente, pero si AMBOS murieran a la
        # vez nadie los relanzaría hasta reiniciar Docker. Joker es PID 1 y
        # sobrevive a todo, así que desde aquí los re-aseguramos cada poco.
        def supervisor_reaseguro_loop():
            interval = float(cfg("TW_SUPERVISOR_REASEGURO", "60") or 60)
            if interval <= 0:
                return
            while True:
                time.sleep(interval)
                try:
                    if not supervisor.watcher_vivo():
                        p = supervisor.ensure_watcher()
                        agent.log.write("supervisor", {"evento": "watcher_reasegurado", "pid": p})
                    if not supervisor.guardian_vivo():
                        p = supervisor.ensure_guardian()
                        agent.log.write("supervisor", {"evento": "guardian_reasegurado", "pid": p})
                except Exception as e:  # noqa: BLE001
                    agent.log.write("error", {"supervisor_reaseguro": str(e)})

        threading.Thread(target=supervisor_reaseguro_loop, daemon=True).start()
        agent.log.write("supervisor", {"evento": "reaseguro_hilo_iniciado"})

    if not token:
        # Sin token: NO se crea el bot de Telegram. El proceso se queda VIVO para
        # servir la API local de turnos (la Superconsola web funciona igual) y no
        # entra en bucle de reinicios bajo `restart: unless-stopped`.
        while True:
            time.sleep(3600)

    tbot = TelegramBot(token, on_message=on_message, on_voice=on_voice,
                       allowed_ids=cfg("TW_TELEGRAM_ALLOWED_USER_IDS"))
    tbot.run_forever()



def main():
    ap = argparse.ArgumentParser(description="NotherClass Agent")
    ap.add_argument("--agent", metavar="ID", default=None,
                    help="id en configAgentes.json (o variable TW_AGENT_ID). Si no se pasa y el "
                         "JSON tiene '_default', se usa ese; si no hay JSON, se usa el .env.")
    ap.add_argument("--list-agents", action="store_true",
                    help="lista los agentes definidos en configAgentes.json")
    ap.add_argument("--oneshot", metavar="QUERY", help="un turno por CLI")
    ap.add_argument("--consolidate", action="store_true",
                    help="ejecuta una consolidación bruto→neto (aprender de verdad)")
    ap.add_argument("--net", action="store_true",
                    help="muestra el estado neto de la memoria")
    args = ap.parse_args()

    if args.list_agents:
        _ags, _path = agentconfig.list_agents()
        print(f"config: {_path}")
        for _aid in sorted(_ags):
            _e = _ags[_aid]
            print(f"  - {_aid}: {_e.get('name', '?')} (store={_e.get('store', '?')})")
        return

    # Config central: aplica configAgentes.json (si existe) sobre el entorno.
    # Tras esto, todos los Agent() leen la config correcta vía cfg()/env.
    agentconfig.bootstrap(args.agent)

    if args.net:
        agent = Agent()
        ns = agent.net_state()
        print(f"creencias {len(ns['beliefs'])} · victorias {ns['lessons_win']} · "
              f"fallos {ns['lessons_fail']} · resúmenes {ns['summaries']} · "
              f"bruto pendiente {ns['pending_bruto']}")
        agent.close()
        return
    if args.consolidate:
        agent = Agent()
        res = agent.consolidate_now()
        if res.get("skipped"):
            print("sin consolidar:", res.get("reason"), f"(bruto leído {res.get('raw_read')})")
        else:
            print(f"bruto leídos: {res.get('raw_read')}")
            print(f"  resumen neto guardado: {bool(res.get('summary_saved'))}")
            print(f"  creencias aplicadas  : {res.get('beliefs_applied')}")
            print(f"  lecciones aplicadas  : {res.get('lessons_applied')}")
        agent.close()
        return
    if args.oneshot:
        run_oneshot(args.oneshot)
    else:
        run_daemon()


if __name__ == "__main__":
    main()

