"""
core/agentconfig.py — Config CENTRAL de agentes (escalable a miles).

Evita tocar un .env por agente: se lee un único configAgentes.json (en la raíz
del repo) que define `_defaults` (valores compartidos), `api_keys` (secretos
centralizados) y `agentes.<id>` (solo lo que difiere de los defaults). Cada
agente se exporta a variables de entorno TW_* antes de arrancar run.py, de modo
que el resto del código (Agent / llm / brain) NO cambia: sigue leyendo cfg().

Uso (nuevo, sin tocar código por agente):
  python run.py --agent joker            # daemon de un agente por id
  python run.py --agent traderx --oneshot "hola"
  python run.py --list-agents            # lista los id disponibles
  # o fija TW_AGENT_ID=joker en el entorno y arranca igual que antes.

Reglas de precedencia (de menor a mayor):
  .env (base) < configAgentes.json (_defaults < entrada del agente).
Las claves de un agente SIEMPRE ganan sobre .env. Referencias:
  "$keys.<nombre>" -> valor de la sección "api_keys".
  "$env.<VAR>"     -> valor de una variable de entorno (secretos fuera del repo).
"""
import json
import os

# Campo humano (JSON) -> variable de entorno TW_* que consume el agente.
FIELD_MAP = {
    "name": "TW_AGENT_NAME",
    "store": "TW_AGENT_STORE",
    "persona": "TW_AGENT_PERSONA",
    "workspace": "TW_WORKSPACE",
    # LLM (Joker)
    "llm_provider": "TW_LLM_PROVIDER",
    "llm_api_key": "TW_LLM_API_KEY",
    "llm_base_url": "TW_LLM_BASE_URL",
    "llm_model": "TW_LLM_MODEL",
    "reasoning_effort": "TW_REASONING_EFFORT",
    "thinking": "TW_THINKING",
    # Tool calling NATIVO (function calling de la API) en vez del protocolo de
    # texto ```tool. Es el arreglo de raíz: el modelo DeepSeek devuelve tool_calls
    # estructurados y no se "escapa" a su formato nativo DSML.
    "native_tools": "TW_NATIVE_TOOLS",
    # Memoria / persona
    "memory_dir": "TW_MEMORY_DIR",
    "context_budget_tokens": "TW_CONTEXT_BUDGET_TOKENS",
    "working_chunks": "TW_WORKING_CHUNKS",
    "recall_max_items": "TW_RECALL_MAX_ITEMS",
    "recall_min_ratio": "TW_RECALL_MIN_RATIO",
    # Telegram
    "telegram_bot_token": "TW_TELEGRAM_BOT_TOKEN",
    "telegram_allowed_user_ids": "TW_TELEGRAM_ALLOWED_USER_IDS",
    # API LOCAL de turnos (para que la WEB use ESTE MISMO agente, ver run_daemon)
    "http_port": "TW_HTTP_PORT",
    # Salida / telemetría
    "max_reply_chars": "TW_MAX_REPLY_CHARS",
    "show_usage_footer": "TW_SHOW_USAGE_FOOTER",
    "agent_max_tokens": "TW_AGENT_MAX_TOKENS",
    # Confirmación / edición
    "confirm_edits": "TW_CONFIRM_EDITS",
    # BigBoss (revisor)
    "enable_review": "TW_ENABLE_REVIEW",
    "review_model": "TW_REVIEW_MODEL",
    "review_base_url": "TW_REVIEW_BASE_URL",
    "review_api_key": "TW_REVIEW_API_KEY",
    "review_reasoning_effort": "TW_REVIEW_REASONING_EFFORT",
    "review_thinking": "TW_REVIEW_THINKING",
    "review_max_rounds": "TW_REVIEW_MAX_ROUNDS",
    "review_max_tokens": "TW_REVIEW_MAX_TOKENS",
    "review_max_fetches": "TW_REVIEW_MAX_FETCHES",
    # Logs / consolidación
    "log_file": "TW_LOG_FILE",
    "max_iters": "TW_MAX_ITERS",
    "tool_out_cap": "TW_TOOL_OUT_CAP",
    "hist_keep": "TW_HIST_KEEP",
    "hist_cap": "TW_HIST_CAP",
    "hist_maxchars": "TW_HIST_MAXCHARS",
    "plan_first": "TW_PLAN_FIRST",
    "plan_min_chars": "TW_PLAN_MIN_CHARS",
    "plan_max_tokens": "TW_PLAN_MAX_TOKENS",
    "show_plan": "TW_SHOW_PLAN",
    "consolidate_every": "TW_CONSOLIDATE_EVERY",
    "consolidate_min_chunks": "TW_CONSOLIDATE_MIN_CHUNKS",
}

_ROOT_CACHE = None


def _here():
    # agent/core -> agent
    return os.path.dirname(os.path.abspath(__file__))


def repo_root():
    global _ROOT_CACHE
    if _ROOT_CACHE is None:
        _ROOT_CACHE = os.path.dirname(os.path.dirname(_here()))  # agent/core -> repo
    return _ROOT_CACHE


def config_path():
    """Ruta del configAgentes.json (override con TW_CONFIG_FILE)."""
    return os.environ.get("TW_CONFIG_FILE") or os.path.join(repo_root(), "configAgentes.json")


def _read(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve(val, api_keys):
    """Resuelve referencias $keys.<x> y $env.<X>; si no, devuelve el valor tal cual."""
    if isinstance(val, str):
        if val.startswith("$keys."):
            return api_keys.get(val[6:], "")
        if val.startswith("$env."):
            return os.environ.get(val[5:], "")
    return val


def list_agents():
    """Devuelve (dict id->entrada, ruta). Útil para inventario/UI de escala."""
    path = config_path()
    data = _read(path)
    return (data.get("agentes") or {}), path


def load(agent_id=None):
    """Carga la config resuelta de un agente.
    agent_id: por CLI o TW_AGENT_ID; si falta, usa "_default" del JSON.
    Devuelve {agent_id, values(merge defaults+entrada), api_keys, path} o None
    si no hay JSON / agente default.
    """
    path = config_path()
    data = _read(path)
    agents = data.get("agentes") or {}
    agent_id = agent_id or os.environ.get("TW_AGENT_ID") or data.get("_default")
    if not agent_id:
        return None
    entry = agents.get(agent_id)
    if not isinstance(entry, dict):
        disponibles = ", ".join(sorted(agents)) or "(ninguno)"
        raise KeyError(f"agente '{agent_id}' no está en {path}. Disponibles: {disponibles}")
    merged = dict(data.get("_defaults") or {})
    for k, v in entry.items():
        if k.startswith("_"):   # ignorar metadatos (p. ej. _desc)
            continue
        merged[k] = v
    return {
        "agent_id": agent_id,
        "values": merged,
        "api_keys": data.get("api_keys") or {},
        "path": path,
    }


def apply(cfg):
    """Exporta la config resuelta a variables de entorno TW_*. Devuelve nº de claves.
    Soporta tokens en los valores: {repo}=raíz del repo, {carpeta}=raíz/<carpeta>."""
    carpeta = cfg["values"].get("carpeta")
    carpeta = str(carpeta).strip() if carpeta else ""
    root = repo_root()

    def _expand(v):
        if isinstance(v, str):
            v = v.replace("{repo}", root)
            if carpeta:
                v = v.replace("{carpeta}", os.path.join(root, carpeta))
        return v

    exported = 0
    for field, envkey in FIELD_MAP.items():
        if field not in cfg["values"]:
            continue
        v = _expand(_resolve(cfg["values"][field], cfg["api_keys"]))
        if v is None:
            continue
        os.environ[envkey] = str(v)
        exported += 1
    return exported


def bootstrap(agent_id=None, verbose=True):
    """Punto único para arrancar: carga y aplica la config del agente al entorno.
    Devuelve nº de claves exportadas (0 si no hay JSON -> se usa .env como antes).
    """
    try:
        cfg = load(agent_id)
    except KeyError as e:
        print(f"[configAgentes] ⚠️ {e}", flush=True)
        return 0
    if cfg is None:
        return 0
    n = apply(cfg)
    if verbose:
        print(f"[configAgentes] agente '{cfg['agent_id']}' ← {cfg['path']} ({n} claves)", flush=True)
    return n


if __name__ == "__main__":
    import sys
    if "--list" in sys.argv:
        ags, path = list_agents()
        print(f"config: {path}")
        for aid in sorted(ags):
            e = ags[aid]
            print(f"  - {aid}: {e.get('name', '?')} (store={e.get('store', '?')})")
        sys.exit(0)
    print(f"exportadas: {bootstrap(sys.argv[1] if len(sys.argv) > 1 else None)}")


