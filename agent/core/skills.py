"""
core/skills.py — "skills" (tools) del agente, estilo OpenClaw pero en nuestro estilo.

Cada skill es una función pura `fn(deps, **args) -> str` (texto para el LLM).
`deps` trae {brain, workspace}. Todo acceso a archivos se confina dentro del
workspace (TW_WORKSPACE) por seguridad.
"""
import json
import os
import re
import subprocess

# Alias del builtin (la skill 'grep' tiene un parámetro llamado `max`, que si no
# lo sombrearía dentro de la función).
_builtin_max = max


def _safe_path(workspace: str, path: str) -> str:
    """Resuelve un path (relativo o absoluto) y lo confina dentro del workspace.

    Acepta rutas absolutas SIEMPRE QUE estén dentro de TW_WORKSPACE (el agente a
    veces da rutas absolutas). Rechaza escapes fuera del workspace por seguridad.
    """
    base = os.path.abspath(workspace)
    if not path:
        return base
    if os.path.isabs(path):
        target = os.path.abspath(path)
    else:
        target = os.path.abspath(os.path.join(base, path))
    if not (target == base or target.startswith(base + os.sep)):
        raise PermissionError(f"Acceso fuera del workspace denegado: {path}")
    return target


def _list_dir(deps, path="."):
    target = _safe_path(deps["workspace"], path)
    if not os.path.isdir(target):
        return f"no es directorio: {path}"
    names = sorted(os.listdir(target))
    return "\n".join(names) if names else "(vacío)"


def _read_file(deps, path, start=None, end=None, max_lines=400):
    target = _safe_path(deps["workspace"], path)
    if not os.path.isfile(target):
        return f"no existe: {path}"
    try:
        with open(target, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return f"error leyendo: {e}"
    n = len(lines)
    # lectura POR PARTES: si el humano/agente pide un rango de líneas, devolvemos solo ese trozo
    if start is not None or end is not None:
        try:
            s = int(start) if start is not None else 1
            e = int(end) if end is not None else n
        except (TypeError, ValueError):
            return "start/end deben ser números de línea (int)."
        s = max(1, s)
        chunk = "".join(lines[s - 1:e])
        return (chunk.strip() if chunk.strip() else f"(rango {s}-{e} vacío o fuera de rango; archivo tiene {n} líneas)")
    # sin rango: si es muy grande NO lo volcamos entero (evita inflar contexto 200k)
    if n > max_lines:
        body = "".join(lines[:max_lines])
        return (body + f"\n…[archivo grande: {n} líneas. Te mostré solo las primeras {max_lines}. "
                       "Para ver más usa read_file con 'start' y 'end' (nº de línea), o lee por "
                       "campos con python/jq si es JSON]")
    return "".join(lines)


def _write_file(deps, path, content=""):
    target = _safe_path(deps["workspace"], path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    # Backup del contenido previo (si existe) por si una escritura parcial o
    # errónea daña el archivo → permite rollback con el .bak.
    if os.path.exists(target):
        try:
            import shutil
            shutil.copy2(target, target + ".bak")
        except Exception:
            pass
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    return f"OK escrito: {path} ({len(content)} chars)"


def _append_file(deps, path, content=""):
    """Añade contenido al final de un archivo (para escribir archivos grandes en
    partes, como se edita un archivo por trozos). Crea el archivo si no existe."""
    target = _safe_path(deps["workspace"], path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        f.write(content)
    return f"OK añadido a: {path} (+{len(content)} chars, total {os.path.getsize(target)} bytes)"


def _grep(deps, pattern, path=".", glob=None, context=0, max=60, ignore_case=False):
    """Busca un patrón (regex) DENTRO de archivos del workspace y devuelve
    'archivo:línea: texto' (+ contexto opcional), sin volcar el archivo entero.

    Es la forma EFICIENTE de descubrir qué funciones hay/para qué se usa algo:
    1 llamada en vez de leer archivos completos. Si no encuentras, cambia el
    patrón o acota con path/glob; no hace falta leer el archivo entero.
    """
    import fnmatch
    try:
        cap = int(max)
    except (TypeError, ValueError):
        cap = 60
    try:
        ctx = _builtin_max(0, int(context))
    except (TypeError, ValueError):
        ctx = 0
    base = _safe_path(deps["workspace"], path)
    root = _safe_path(deps["workspace"], ".")
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        return f"regex inválida: {e}"
    SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "venv311",
            ".audio_tmp", "MEMORY", "logs"}

    if os.path.isfile(base):
        files = [base]
    else:
        files = []
        for dirpath, dirnames, names in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP and not d.startswith(".")]
            for nm in names:
                if glob and not fnmatch.fnmatch(nm, glob):
                    continue
                fp = os.path.join(dirpath, nm)
                try:
                    if os.path.getsize(fp) > 2_000_000:
                        continue
                except OSError:
                    continue
                files.append(fp)

    hits = []
    seen = set()
    nfiles = 0
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception:  # noqa: BLE001
            continue
        nfiles += 1
        rel = os.path.relpath(fp, root)
        for i, ln in enumerate(lines):
            if not rx.search(ln):
                continue
            lo = _builtin_max(0, i - ctx)
            hi = min(len(lines), i + ctx + 1)
            for k in range(lo, hi):
                key = (rel, k)
                if key in seen:
                    continue
                seen.add(key)
                mark = ":" if k == i else "-"
                hits.append(f"{rel}{mark}{k + 1}{mark} {lines[k].rstrip()}")
                if len(hits) >= cap:
                    break
            if len(hits) >= cap:
                break
        if len(hits) >= cap:
            break

    if not hits:
        return (f"sin coincidencias para {pattern!r} (busqué en {nfiles} archivos). "
                f"Prueba otro patrón, añade glob (p.ej. *.py) o cambia path.")
    body = "\n".join(hits)
    if len(hits) >= cap:
        body += f"\n…[tope {cap} coincidencias alcanzado; afina el patrón o usa path/glob]"
    return body


def _edit_file(deps, path, find, replace="", all=False):
    """Edita un archivo reemplazando un TROZO EXACTO (find) por otro (replace),
    SIN reescribir todo el archivo. Es la forma ROBUSTA y barata de editar (como
    el 'str_replace' de Cline): gasta poquísimos tokens y no arriesga el resto.

    find debe coincidir EXACTAMENTE (espacios/sangría/saltos incluidos). Si no
    encuentra, devuelve una pista para que use grep y copie el fragmento real.
    """
    target = _safe_path(deps["workspace"], path)
    if not os.path.isfile(target):
        return f"no existe: {path} (usa write_file para crearlo)"
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = f.read()
    except Exception as e:  # noqa: BLE001
        return f"error leyendo {path}: {e}"
    if not find:
        return "find vacío: indica el texto EXACTO (literal) a reemplazar."
    n = data.count(find)
    if n == 0:
        return (f"NO encontré ese texto exacto en {path}. Debe coincidir EXACTAMENTE "
                f"(incluye espacios y saltos). Usa grep para ver el fragmento REAL y "
                f"cópialo tal cual; o usa write_file si vas a rehacer el archivo.")
    if n > 1 and not all:
        return (f"El texto aparece {n} veces en {path}. Para no editar el sitio "
                f"equivocado, amplía 'find' con más contexto (para que sea único) "
                f"o pasa all=true para reemplazarlas todas.")
    nuevo = data.replace(find, replace) if all else data.replace(find, replace, 1)
    try:
        import shutil
        shutil.copy2(target, target + ".bak")
    except Exception:  # noqa: BLE001
        pass
    with open(target, "w", encoding="utf-8") as f:
        f.write(nuevo)
    k = n if all else 1
    return f"OK editado: {path} ({k} reemplazo(s); {len(find)}→{len(replace)} chars)"


def _mapa(deps, path=".", glob=None, max_files=300):
    """Índice COMPACTO del código (un "mapa"): por archivo, su ruta, tamaño y los
    def/class de primer nivel (en .py). Sirve para SABER DÓNDE está cada cosa con
    UNA consulta barata, en vez de leer archivos enteros o grepear a ciegas.
    Se regenera al momento (sin LLM) y se cachea en <memoria>/mapa_codigo.txt."""
    import fnmatch
    base = _safe_path(deps["workspace"], path)
    root = _safe_path(deps["workspace"], ".")
    SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "venv311",
            ".audio_tmp", "MEMORY", "logs"}
    defs_re = re.compile(r"^(?:async\s+)?def\s+(\w+)|^class\s+(\w+)")
    lineas = []
    n = 0
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if d not in SKIP and not d.startswith(".")]
        for fn in sorted(fns):
            if fn.endswith(".bak") or fn.endswith(".pyc"):
                continue
            if glob and not fnmatch.fnmatch(fn, glob):
                continue
            fp = os.path.join(dp, fn)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            rel = os.path.relpath(fp, root)
            extra = ""
            if fn.endswith(".py"):
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        names = []
                        for ln in f:
                            m = defs_re.match(ln)
                            if m:
                                names.append(m.group(1) or m.group(2))
                            if len(names) >= 25:
                                names.append("…")
                                break
                    if names:
                        extra = " · defs: " + ", ".join(names)
                except Exception:  # noqa: BLE001
                    pass
            lineas.append(f"{rel}  ({sz}B){extra}")
            n += 1
            if n >= max_files:
                break
        if n >= max_files:
            break
    txt = "\n".join(lineas) if lineas else "(vacío)"
    try:
        mem = getattr(deps.get("brain"), "memory_dir", None)
        if mem:
            with open(os.path.join(mem, "mapa_codigo.txt"), "w", encoding="utf-8") as f:
                f.write(txt)
    except Exception:  # noqa: BLE001
        pass
    return f"(mapa: {n} archivos bajo '{path}')" + "\n" + txt


def _run_shell(deps, command, timeout=60):
    try:
        res = subprocess.run(command, shell=True, cwd=_safe_path(deps["workspace"], "."),
                             capture_output=True, text=True, timeout=timeout)
        out = (res.stdout or "")[-4000:]
        err = (res.stderr or "")[-1000:]
        return (out + ("\n[stderr]\n" + err if err else "")).strip() or "(sin salida)"
    except subprocess.TimeoutExpired:
        return "comando agotó el timeout"
    except Exception as e:
        return f"error: {e}"


def _recall(deps, query, budget=2000):
    brain = deps["brain"]
    res = brain.recall(query, budget_tokens=budget)
    parts = []
    for it in res["working"]:
        parts.append("[reciente] " + (it["text"] or ""))
    for it in res["episodic"]:
        parts.append("[memoria] " + (it["text"] or ""))
    for b in res["beliefs"]:
        parts.append(f"[creencia:{b['key']}={b['value']} conf={b['confidence']:.2f}]")
    return "\n".join(parts) if parts else "(no hay memoria relevante)"


def _remember(deps, text, kind="event"):
    cid = deps["brain"].remember(text, kind=kind)
    return f"memorizado ({cid})"


def _learn(deps, kind, title, body="", outcome=""):
    deps["brain"].learn(kind, title, body, outcome)
    return f"lección guardada: {kind} — {title}"


def _set_belief(deps, key, value, confidence=0.7):
    deps["brain"].set_belief(key, value, confidence=float(confidence))
    return f"creencia guardada: {key} = {value} (conf {confidence})"


def _show_lessons(deps, kind=None):
    from brain import lessons as _lessons_mod
    rows = deps["brain"].lessons(kind=kind, limit=15)
    return _lessons_mod.format_lessons(rows)


# ── Gestión de agentes (PODER solo del agente creador, p. ej. Joker/Forja) ──
def _es_creador(deps=None):
    """Solo el/los store(s) listados en TW_CREATOR_STORES (default Forja) pueden
    crear/eliminar agentes. Evita que un agente hijo cree/borre a otros."""
    allow = {x.strip() for x in os.environ.get("TW_CREATOR_STORES", "Forja").split(",") if x.strip()}
    return os.environ.get("TW_AGENT_STORE", "") in allow


def _guardar_config(ws, data):
    cfgp = os.path.join(ws, "configAgentes.json")
    tmp = cfgp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, cfgp)
    return cfgp


def _crear_agente(deps, id, name=None, rol="", telegram_token="", desc="",
                  persona="", values=""):
    """Crea un agente nuevo en su carpeta Ag.<Nombre>/ (tipo 'ag'). Solo creador.

    `persona` = personalidad COMPLETA en markdown (la redacta el agente tras
    entrevistar al humano). `values` = valores/reglas que debe seguir.
    El agente nuevo nace DESACTIVADO (_supervisar: false) a propósito: así no
    arranca en bucle intentando llamar al LLM sin token ni proveedor.
    """
    if not _es_creador(deps):
        return ("⛔ Acceso denegado: solo el agente creador (store Forja) puede crear "
                "agentes. Tú eres el store " + os.environ.get("TW_AGENT_STORE", "?") + ".")
    id = (id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", id):
        return f"⚠️ id inválido: {id!r}. Usa letras/números/_/- (sin espacios)."
    try:
        from . import agentes as _am
    except Exception:  # noqa: BLE001
        from core import agentes as _am
    ws = deps["workspace"]
    cfgp = os.path.join(ws, "configAgentes.json")
    if not os.path.exists(cfgp):
        return f"no existe la config central: {cfgp}"
    try:
        with open(cfgp, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        return f"error leyendo config: {e}"
    agents = data.setdefault("agentes", {})
    if id in agents:
        return f"ya existe el agente '{id}' en configAgentes.json."
    name = (name or id).strip()
    carpeta, entry, fdir, err = _am.crear_carpeta(
        id, name, rol=rol or "", desc=desc or "", token=telegram_token, workspace=ws,
        persona=persona or "", values=values or "")
    if err:
        return f"⚠️ {err}"
    agents[id] = entry
    _guardar_config(ws, data)

    # ── Instrucciones para el HUMANO (esto es lo que el agente debe contarle) ──
    tiene_token = bool(entry.get("telegram_bot_token"))
    lineas = [
        f"✔ Agente «{name}» (id={id}) creado.",
        "",
        "📂 ESTRUCTURA (todo dentro de su carpeta, no se mezcla con nadie):",
        f"   • Personalidad ....... {carpeta}/persona.md      ← AQUÍ defines quién es",
        f"   • Su memoria (BD) .... {carpeta}/memory/brain_{id}.sqlite3  ← se crea sola",
        f"   • Su registro ........ {carpeta}/logs/agent.log",
        f"   • Su registro en ..... configAgentes.json → agentes.{id}",
        "",
        "⚙️  ESTADO: DESACTIVADO a propósito (\"_supervisar\": false).",
        "    Así NO arranca solo ni gasta tokens hasta que tú lo actives.",
        "",
    ]
    if tiene_token:
        lineas += [
            "✅ Token de Telegram: YA CONFIGURADO.",
            f"   Prueba:  cd agent && python3 run.py --agent {id}",
        ]
    else:
        lineas += [
            "⚠️ FALTA lo siguiente para que funcione:",
            "",
            "   1) SU TOKEN DE TELEGRAM (obligatorio para hablarle desde el móvil):",
            "      · Abre Telegram y habla con @BotFather",
            "      · Envía /newbot y elige nombre y usuario para el bot",
            "      · Copia el token (tiene forma 123456789:AAF-xxxxxxxxxxxx)",
            f"      · Dámelo y lo pongo yo, o edítalo tú en configAgentes.json:",
            f"          \"agentes\": {{ \"{id}\": {{ \"telegram_bot_token\": \"TU_TOKEN\" }} }}",
            "      · Si lo prefieres en el .env (recomendado, no queda en el repo):",
            "          paso 1 → añade en agent/.env:   TW_TOKEN_" + id.upper() + "=tu_token",
            f"          paso 2 → en configAgentes.json pon: \"telegram_bot_token\": \"$env.TW_TOKEN_{id.upper()}\"",
            "",
            "   ⚠️ IMPORTANTE: el token debe ser ÚNICO. Si dos agentes usan el mismo,",
            "      Telegram solo atiende a uno y el otro da error 409 Conflict.",
            "",
            "   2) SU PERSONALIDAD: edita " + carpeta + "/persona.md",
            "      (quién es, qué hace, cómo habla). Sin esto, responde genérico.",
        ]
    lineas += [
        "",
        "🚀 CUANDO ESTÉ LISTO, actívalo:",
        f"   • En configAgentes.json, en \"agentes.{id}\", pon:  \"_supervisar\": true",
        "   • Y arráncalo:",
        f"         cd agent && python3 run.py --agent {id}",
        "     (o reinicia el contenedor/servicio y el supervisor lo levanta solo)",
        "",
        "🧠 Hereda automáticamente tu proveedor de LLM del .env (TW_LLM_*).",
        "   Si quieres que use OTRO modelo, añade en su entrada:",
        "      \"llm_provider\": \"...\", \"llm_model\": \"...\", \"llm_api_key\": \"$env.TU_CLAVE\"",
        "",
        "💡 Para probarlo en un GRUPO con otro agente (y el coordinador), ver GRUPO_Y_REVISION.md.",
    ]
    return "\n".join(lineas)


def _eliminar_agente(deps, id):
    """Elimina un agente de configAgentes.json (y su carpeta Ag.<Nombre>/). Solo creador."""
    if not _es_creador(deps):
        return ("⛔ Acceso denegado: solo el agente creador (store Forja) puede "
                "eliminar agentes.")
    id = (id or "").strip()
    me = os.environ.get("TW_AGENT_STORE", "")
    if id in ("Forja", "joker", me) or not id:
        return f"⛔ No puedo eliminar al creador/actual ('{id}')."
    ws = deps["workspace"]
    cfgp = os.path.join(ws, "configAgentes.json")
    if not os.path.exists(cfgp):
        return f"no existe la config central: {cfgp}"
    try:
        with open(cfgp, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        return f"error leyendo config: {e}"
    agents = data.get("agentes", {})
    if id not in agents:
        return f"no existe el agente '{id}' en configAgentes.json."
    entry = agents[id]
    carpeta = entry.get("carpeta") if isinstance(entry, dict) else None
    del agents[id]
    _guardar_config(ws, data)
    if carpeta:
        import shutil
        shutil.rmtree(os.path.join(ws, carpeta), ignore_errors=True)
    return (f"🗑 Agente '{id}' eliminado de configAgentes.json"
            + (f" (y su carpeta {carpeta}/)." if carpeta else "."))


SKILLS = {
    "list_dir": {"desc": "Lista archivos de una carpeta del workspace. args: {path}",
                 "fn": _list_dir},
    "read_file": {"desc": "Lee un archivo. Para archivos GRANDES usa start y end (nº de línea) para leer POR PARTES y no volcar todo. args: {path, start?, end?}",
                  "fn": _read_file},
    "write_file": {"desc": "Crea/sobrescribe un archivo. USALO SIEMPRE con la sintaxis literal SIN JSON: ```file ruta\\n<contenido del archivo>\\n```",
                   "fn": _write_file},
    "append_file": {"desc": "Añade al FINAL de un archivo (continuar por partes). USALO con la sintaxis literal SIN JSON: ```append ruta\\n<contenido>\\n```",
                    "fn": _append_file},
    "run_shell": {"desc": "Ejecuta un comando shell en el workspace. args: {command, timeout?}",
                  "fn": _run_shell},
    "grep": {"desc": "Busca un patrón (regex) DENTRO de archivos y devuelve 'archivo:línea: texto'. Úsalo para DESCUBRIR funciones/uso rápido en vez de leer archivos enteros. args: {pattern, path?, glob?, context?, max?}",
             "fn": _grep},
    "edit_file": {"desc": "PREFERIDO para editar: reemplaza un trozo EXACTO (find) por otro (replace) SIN reescribir el archivo entero (gasta poquísimos tokens; como el str_replace de Cline). args: {path, find, replace?, all?}",
                  "fn": _edit_file},
    "mapa": {"desc": "Índice COMPACTO del código: por archivo, su tamaño y los def/class de primer nivel. Úsalo para SABER DÓNDE está algo con 1 llamada barata ANTES de leer o grepear. args: {path?, glob?, max_files?}",
             "fn": _mapa},
    "recall": {"desc": "Busca en TU memoria híbrida. args: {query}",
               "fn": _recall},
    "remember": {"desc": "Guarda algo en TU memoria. args: {text, kind?}",
                 "fn": _remember},
    "learn": {"desc": "Registra una lección. args: {kind:'win'|'fail', title, body, outcome}",
              "fn": _learn},
    "set_belief": {"desc": "Guarda una creencia. args: {key, value, confidence?}",
                   "fn": _set_belief},
    "show_lessons": {"desc": "Muestra lecciones (win/fail) recientes. args: {kind?}",
                     "fn": _show_lessons},
    "crear_agente": {"desc": (
        "Crea un agente nuevo (solo el agente creador) en Ag.<Nombre>/. "
        "args: {id, name, rol, persona, values, telegram_token, desc}. "
        "PROCESO OBLIGATORIO — NO crees el agente a la primera: PRIMERO ENTREVISTA al humano "
        "y reúne TODOS los datos. Pregúntale, en lenguaje natural: "
        "(1) ¿cómo se llamará? (2) ¿qué debe hacer exactamente (su rol/propósito)? "
        "(3) ¿cómo quieres que hable (tono, estilo, idioma)? (4) ¿qué valores o reglas debe seguir? "
        "(5) ¿tiene token de Telegram? si no, indícale crearlo con @BotFather y pídeselo. "
        "(6) ¿tiene tareas/horarios o datos concretos que deba conocer? "
        "Haz las preguntas de forma natural y en varios mensajes si hace falta, sin abrumar. "
        "Cuando tengas TODO, pasa 'persona' con la personalidad COMPLETA ya redactada (en markdown) "
        "y 'rol' con su propósito. Luego crea el agente y resume al humano: dónde editar su persona, "
        "dónde vive su memoria, y que queda DESACTIVADO hasta poner \"_supervisar\": true. "
        "El token de Telegram debe ser ÚNICO por agente (si se repite: error 409)."),
                     "fn": _crear_agente},
    "eliminar_agente": {"desc": "Elimina un agente y su carpeta (solo el agente creador Forja). args: {id}", "fn": _eliminar_agente},
}


# ── Esquemas NATIVOS (function calling) por skill ──────────────────────────
# Permiten usar el tool-calling de la API (tools/tool_calls) en vez del protocolo
# de texto. El modelo devuelve los argumentos ya estructurados (JSON), sin las
# "fugas" a formatos nativos (DSML) ni problemas de escape.
PARAMS = {
    "list_dir":       {"path": {"type": "string", "description": "ruta de la carpeta"}},
    "read_file":      {"path": {"type": "string"},
                       "start": {"type": "integer", "description": "línea inicial (1-based)"},
                       "end": {"type": "integer", "description": "línea final (inclusive)"}},
    "write_file":     {"path": {"type": "string"}, "content": {"type": "string", "description": "contenido completo del archivo"}},
    "append_file":    {"path": {"type": "string"}, "content": {"type": "string"}},
    "run_shell":      {"command": {"type": "string"}, "timeout": {"type": "integer"}},
    "grep":           {"pattern": {"type": "string", "description": "regex a buscar"},
                       "path": {"type": "string", "description": "archivo o carpeta (default '.')"},
                       "glob": {"type": "string", "description": "filtro de nombres, p.ej. *.py"},
                       "context": {"type": "integer", "description": "líneas de contexto alrededor"},
                       "max": {"type": "integer", "description": "máximo de coincidencias"}},
    "edit_file":      {"path": {"type": "string"},
                       "find": {"type": "string", "description": "texto EXACTO a reemplazar"},
                       "replace": {"type": "string", "description": "texto nuevo (vacío = borrar)"},
                       "all": {"type": "boolean", "description": "reemplazar todas las apariciones"}},
    "mapa":           {"path": {"type": "string", "description": "carpeta a indexar (default '.')"},
                       "glob": {"type": "string", "description": "filtro de nombres, p.ej. *.py"},
                       "max_files": {"type": "integer", "description": "máximo de archivos"}},
    "recall":         {"query": {"type": "string"}, "budget": {"type": "integer"}},
    "remember":       {"text": {"type": "string"}, "kind": {"type": "string"}},
    "learn":          {"kind": {"type": "string", "enum": ["win", "fail"]},
                       "title": {"type": "string"}, "body": {"type": "string"}, "outcome": {"type": "string"}},
    "set_belief":     {"key": {"type": "string"}, "value": {"type": "string"}, "confidence": {"type": "number"}},
    "show_lessons":   {"kind": {"type": "string"}},
    "crear_agente":   {"id": {"type": "string", "description": "identificador corto (letras/números/_-)"},
                       "name": {"type": "string", "description": "nombre visible del agente"},
                       "rol": {"type": "string", "description": "su propósito en una frase"},
                       "persona": {"type": "string", "description": "PERSONALIDAD COMPLETA en markdown (quién es, cómo habla, qué hace). Redáctala TÚ tras entrevistar al humano."},
                       "values": {"type": "string", "description": "valores y reglas que debe seguir (si no redactaste la persona entera)"},
                       "telegram_token": {"type": "string", "description": "token del bot de @BotFather. PÍDELO al humano si no lo tienes. Debe ser único."},
                       "desc": {"type": "string", "description": "descripción corta para el config"}},
    "eliminar_agente": {"id": {"type": "string"}},
}
REQUIRED = {
    "list_dir": ["path"], "read_file": ["path"], "write_file": ["path", "content"],
    "append_file": ["path", "content"], "run_shell": ["command"], "grep": ["pattern"],
    "edit_file": ["path", "find"],
    "mapa": [],
    "recall": ["query"],
    "remember": ["text"], "learn": ["kind", "title"], "set_belief": ["key", "value"],
    "show_lessons": [], "crear_agente": ["id"], "eliminar_agente": ["id"],
}


def _tool_desc(name: str) -> str:
    """Descripción corta para el schema nativo (sin la coletilla 'args: {...}')."""
    d = SKILLS.get(name, {}).get("desc", name)
    return d.split("args:")[0].strip().rstrip(".")


def tools_spec():
    """Devuelve las tools en formato OpenAI (function calling) para la API."""
    out = []
    for name in SKILLS:
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": _tool_desc(name),
                "parameters": {"type": "object",
                               "properties": PARAMS.get(name, {}),
                               "required": REQUIRED.get(name, [])},
            },
        })
    return out

TOOL_BLOCK_RE = re.compile(
    r"```tool\s+([a-z_]+)\s*\n(.*?)(?:```|$)", re.IGNORECASE | re.DOTALL)

# Sintaxis simple para escribir archivos SIN JSON (evita los errores de escape
# y el cierre que se olvida):  ```file ruta\n<contenido literal>\n```   y
#                              ```append ruta\n<contenido literal>\n```
FILE_BLOCK_RE = re.compile(
    r"```(file|append)\s+([^\n]+)\n(.*?)(?:```|$)", re.IGNORECASE | re.DOTALL)


def tool_descriptions() -> str:
    return "\n".join(f"- {name}: {meta['desc']}" for name, meta in SKILLS.items())


def _salvage_args(name: str, raw: str) -> dict:
    """Recupera argumentos de un tool-block. Para escribir archivos, si el JSON
    llegó MAL FORMADO, NO devolvemos contenido (evita corromper el archivo con
    escapes \n y colas basura): devolvemos una marca de error para que el agente
    reescriba con la sintaxis literal ```file / ```append."""
    if name in ("write_file", "append_file"):
        m = re.search(r'"path"\s*:\s*"([^"]*)"', raw)
        path = m.group(1) if m else ""
        return {"_broken": True, "path": path}
    return {"raw": raw}


XML_INVOKE_RE = re.compile(
    r'<invoke\s+name=["\'](\w+)["\']\s*>(.*?)</invoke>',
    re.IGNORECASE | re.DOTALL)
XML_PARAM_RE = re.compile(
    r'<parameter\s+name=["\'](\w+)["\'](?:\s+string=["\']?(?:true|false)["\']?)?\s*>(.*?)</parameter>',
    re.IGNORECASE | re.DOTALL)


def _dsml_to_xml(text: str) -> str:
    """Normaliza el marcado DSML nativo de DeepSeek a XML plano.

    El modelo puede emitir tool-calls con el token DSML (barras de ancho completo
    U+FF5C), p. ej. ``<｜｜DSML｜｜invoke name="run_shell">``. Aquí se quita el
    prefijo DSML -> ``<invoke name="run_shell">`` para que el parser XML de abajo
    lo reconozca (red de seguridad; el camino principal es el tool-calling nativo).
    """
    if "\uff5c" not in text and "DSML" not in text:
        return text
    text = re.sub(r"<[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*([^>]*?)\s*>", r"<\1>", text)
    text = re.sub(r"</[\uff5c|]*\s*DSML\s*[\uff5c|]*\s*([^>]*?)\s*>", r"</\1>", text)
    return text


def _parse_xml_invokes(text: str):
    """Normaliza tool-calls en formato NATIVO del modelo (XML tipo Qwen/DeepSeek,
    o DSML de DeepSeek) a la misma tupla (nombre, args) que parse_tool_calls:
    <invoke name="run_shell"><parameter name="command">...</parameter></invoke>
    """
    calls = []
    for m in XML_INVOKE_RE.finditer(_dsml_to_xml(text)):
        name = m.group(1).strip().lower()
        body = m.group(2)
        args = {}
        for pm in XML_PARAM_RE.finditer(body):
            args[pm.group(1).strip()] = pm.group(2).strip()
        calls.append((name, args))
    return calls


def stub_file_blocks(text: str) -> str:
    """Sustituye el CUERPO de los bloques ```file/```append por un stub, para no
    reenviar el contenido completo en el historial (el archivo YA está en disco).
    Es el equivalente de _stub_native_calls para el protocolo de texto."""
    def _rep(m):
        mode = m.group(1).lower()
        path = m.group(2).strip()
        n = len(m.group(3))
        return (f"```{mode} {path}\n[omitido en el historial: {n} chars; el archivo YA "
                f"está en disco. Usa read_file si necesitas verlo.]\n```")
    return FILE_BLOCK_RE.sub(_rep, text or "")


def parse_tool_calls(text: str):
    """Extrae llamadas del texto del agente. Acepta:

    - ```file ruta\n<contenido literal>```      -> write_file (sin JSON)
    - ```append ruta\n<contenido literal>```    -> append_file (sin JSON)
    - ```tool nombre\n{json}```                 -> el resto de tools
    - bloques SIN cierre ``` (se toma hasta el final) -> no se pierde nada.
    """
    calls = _parse_xml_invokes(text)
    for m in FILE_BLOCK_RE.finditer(text):
        mode = m.group(1).lower()
        path = m.group(2).strip()
        content = m.group(3)
        name = "write_file" if mode == "file" else "append_file"
        calls.append((name, {"path": path, "content": content}))
    for m in TOOL_BLOCK_RE.finditer(text):
        name = m.group(1).strip().lower()
        raw = m.group(2).strip()
        try:
            args = json.loads(raw) if raw else {}
        except Exception:
            args = _salvage_args(name, raw)
        calls.append((name, args))
    return calls


# ── Detección y limpieza de "marcado de herramienta" en el texto visible ──────
# El modelo a veces emite sus tool-calls en formato NATIVO (XML) o deja bloques
# ```tool/```file/```append crudos. Nada de eso debe llegar NUNCA al humano.
XML_BLOCK_RE = re.compile(r"<(?:tool_)?calls\b.*?</(?:tool_)?calls>", re.IGNORECASE | re.DOTALL)
XML_INVOKE_LOOSE_RE = re.compile(r"<invoke\b.*?</invoke>", re.IGNORECASE | re.DOTALL)
FENCE_TOOL_RE = re.compile(r"```(?:tool|file|append)\b[^\n]*\n.*?(?:```|$)",
                           re.IGNORECASE | re.DOTALL)


def has_raw_tool_markup(text: str) -> bool:
    """¿El texto contiene marcas de herramienta (XML/DSML nativo o fences crudos)?
    Sirve para (1) forzar un resumen en texto y (2) evitar mostrar basura."""
    if not text:
        return False
    t = _dsml_to_xml(text)
    return bool(XML_BLOCK_RE.search(t) or XML_INVOKE_LOOSE_RE.search(t)
                or FENCE_TOOL_RE.search(t))


def strip_tool_markup(text: str) -> str:
    """Quita TODO el marcado de herramienta (XML <tool_calls>/<invoke>, DSML de
    DeepSeek y fences ```tool/```file/```append) del texto que va al humano.
    Garantiza que nunca se filtre una llamada cruda aunque el parser no la haya
    reconocido."""
    if not text:
        return text
    t = _dsml_to_xml(text)
    t = XML_BLOCK_RE.sub("", t)
    t = XML_INVOKE_LOOSE_RE.sub("", t)
    t = FENCE_TOOL_RE.sub("", t)
    return t.strip()


def execute_tool_call(name: str, args: dict, deps: dict) -> str:
    if name not in SKILLS:
        return f"skill desconocida: {name}. Disponibles: {', '.join(SKILLS)}"
    try:
        if not isinstance(args, dict):
            args = {}
        if args.pop("_broken", False):
            p = args.get("path", "ruta/archivo")
            return (f"⚠️ El JSON de {name} llegó MAL FORMADO y NO se escribió nada "
                    f"(evité corromper el archivo). Reescríbelo con la sintaxis literal "
                    f"SIN JSON:\n```file {p}\n<contenido literal>\n```"
                    if name == "write_file" else
                    f"⚠️ El JSON de {name} llegó MAL FORMADO y NO se escribió nada. "
                    f"Usa:\n```append {p}\n<contenido literal>\n```")
        # args raw sin json → intenta tratarlo como texto del primer arg
        if "raw" in args and isinstance(args.get("raw"), str):
            args = {"query": args["raw"]}
        return SKILLS[name]["fn"](deps, **args)
    except TypeError as e:
        return f"argumentos inválidos para {name}: {e}"
    except Exception as e:
        return f"error en {name}: {e}"


