"""
core/llm.py — capa multi-proveedor OpenAI-compatible para hablar con el modelo.

Mismo patrón que tu PROVIDERS de ai_agent.py (deepseek / glm / …), pero leyendo
la config desde el entorno (python-dotenv o variables) y usando solo la stdlib.

Cualquier API compatible con `POST {base}/chat/completions {model, messages}`
funciona: DeepSeek, z.ai (GLM), OpenRouter, OpenAI, Anthropic-vía-compat, etc.
"""
import json
import os
import time
import urllib.request
from typing import Optional

# Defaults por proveedor (base_url, modelo)
_DEFAULTS = {
    "deepseek": ("https://api.deepseek.com/v1/chat/completions", "deepseek-chat"),
    "glm": ("https://api.z.ai/api/paas/v4/chat/completions", "glm-4.7-flashx"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", None),
    "openai": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    "anthropic": ("https://api.anthropic.com/v1/chat/completions", None),
}

_ENV = os.environ

# ─── grabador de conversación (para inspección/visualización) ───────────────
_recording = False
_record: list = []


def set_recording(on: bool):
    """Activa/desactiva la grabación de cada request (mensajes + respuesta)."""
    global _recording
    _recording = on


def record_clear():
    _record.clear()


def record_get():
    return list(_record)


def _get(key, default=""):
    return _ENV.get(key, default)


def _load_dotenv(path: str = ".env"):
    """Carga .env local si existe (python-dotenv opcional → parse manual mínimo)."""
    if not os.path.exists(path):
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(path, override=False)
    except Exception:
        # parse manual mínimo, ignora comillas y comentarios.
        # OJO: se usa asignación directa (NO setdefault) a propósito. Docker
        # Compose con `env_file:` declara las variables con valor VACÍO en el
        # entorno, así que con setdefault el valor del .env nunca se aplicaba:
        # el agente decía "No hay TW_TELEGRAM_BOT_TOKEN" aunque estuviera puesto
        # (bug detectado probando el arranque en Docker).
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                # no pisar una variable REAL del entorno con un valor vacío
                if v or not _ENV.get(k):
                    _ENV[k] = v


def resolve_provider_cfg(provider: Optional[str] = None) -> dict:
    """Devuelve {base_url, api_key, model} resueltos para el proveedor activo.

    Prioridad de la clave (de mayor a menor):
      1. TW_LLM_API_KEY  (lo que rellenas en tu .env — LO NORMAL)
      2. la variable del proveedor (DEEPSEEK_API_KEY / ZAI_API_KEY / OPENAI_API_KEY),
         solo como compatibilidad si NO has puesto nada en TW_LLM_API_KEY.

    ⚠️ Nunca se usa una clave "a medias": si TW_LLM_API_KEY tiene algo, se usa ESA.
    """
    provider = (provider or _get("TW_LLM_PROVIDER", "deepseek") or "deepseek").lower()
    default_base, default_model = _DEFAULTS.get(provider, (None, None))
    base_url = _get("TW_LLM_BASE_URL") or default_base or ""
    model = _get("TW_LLM_MODEL") or default_model or ""
    api_key = (_get("TW_LLM_API_KEY") or "").strip()
    if not api_key:
        # Compatibilidad: si no definiste TW_LLM_API_KEY, se acepta la variable
        # clásica del proveedor. (Si TW_LLM_API_KEY tiene valor, se respeta.)
        _compat = {"deepseek": "DEEPSEEK_API_KEY", "glm": "ZAI_API_KEY",
                   "openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
        var = _compat.get(provider)
        if var:
            api_key = (_get(var) or "").strip()
    if not base_url:
        base_url = "https://api.deepseek.com/v1/chat/completions"
    if not model:
        model = "deepseek-chat"
    return {"base_url": base_url, "api_key": api_key, "model": model, "provider": provider}


# ─── Precios en USD por 1M tokens ──────────────────────────────────────────
# DeepSeek v4 (deepseek-flash / deepseek-v4-pro): (cache_hit, cache_miss, output)
# en OFF-PEAK. En PEAK (01:00-04:00 y 06:00-10:00 UTC, L-V) se DUPLICAN.
_DEEPSEEK_TIERS = {
    "flash": (0.007, 0.22, 0.66),   # deepseek-flash (DeepSeek-V4.1-Flash)
    "pro":   (0.022, 0.66, 1.98),   # deepseek-v4-pro
}
# GLM (z.ai) en USD por 1M tokens: (cache_hit, cache_miss, output).
# Fuente: https://docs.z.ai/guides/overview/pricing (consultado 2026-09-13).
_GLM_TIERS = {
    "glm-5.3-flash": (0.03, 0.15, 0.50),   # el que usamos nosotros
    "glm-5.3": (0.26, 1.40, 4.40),
    "glm-5.2": (0.26, 1.40, 4.40),
    "glm-5.1": (0.26, 1.40, 4.40),
    "glm-5": (0.20, 1.00, 3.20),
    "glm-4.7-flashx": (0.01, 0.07, 0.40),
    "glm-4.7": (0.11, 0.60, 2.20),
    "glm-4.6": (0.11, 0.60, 2.20),
    "glm-4.5-airx": (0.22, 1.10, 4.50),
    "glm-4.5-air": (0.03, 0.20, 1.10),
    "glm-4.5-x": (0.45, 2.20, 8.90),
    "glm-4.5": (0.11, 0.60, 2.20),
    "glm-4.7-flash": (0.0, 0.0, 0.0),      # GRATIS
    "glm-4.5-flash": (0.0, 0.0, 0.0),      # GRATIS
}
# Genéricos (miss_in, out) para otros proveedores; hit ~2% del miss.
_PRICING_GEN = {
    "openai": (2.50, 10.00),
}


def _deepseek_tier(model: str) -> str:
    return "pro" if "pro" in (model or "").lower() else "flash"


def _glm_tier(model: str) -> str:
    """Clave de tarifa GLM del modelo (tolera sufijos/prefijos raros)."""
    m = (model or "").lower()
    for k in sorted(_GLM_TIERS, key=len, reverse=True):
        if k in m:
            return k
    return "glm-5.3-flash"      # el nuestro por defecto


def _is_peak(now=None) -> bool:
    """DeepSeek: PEAK = 01:00-04:00 y 06:00-10:00 UTC, de lunes a viernes."""
    from datetime import datetime, timezone
    d = now or datetime.now(timezone.utc)
    return d.weekday() < 5 and (1 <= d.hour < 4 or 6 <= d.hour < 10)


def _estimate_cost(provider: str, usage: dict, model: str = "") -> float:
    hit = usage.get("prompt_cache_hit_tokens", 0) or 0
    miss = usage.get("prompt_cache_miss_tokens", 0) or 0
    pt = usage.get("prompt_tokens", 0) or 0
    ct = usage.get("completion_tokens", 0) or 0
    if provider == "kimi":                      # K2.7 Code: hit .19 / miss .95 / out 4.00
        if hit or miss:
            return (miss / 1e6 * 0.95) + (hit / 1e6 * 0.19) + (ct / 1e6 * 4.00)
        return (pt / 1e6 * 0.95) + (ct / 1e6 * 4.00)
    if provider == "deepseek":
        ph, pm, po = _DEEPSEEK_TIERS[_deepseek_tier(model)]
        if _is_peak():
            ph, pm, po = ph * 2, pm * 2, po * 2
    elif provider in ("glm", "zai"):
        # GLM tiene precio REAL de caché (no el ~2% genérico): 0.03 vs 0.15.
        ph, pm, po = _GLM_TIERS[_glm_tier(model)]
    else:
        pm, po = _PRICING_GEN.get(provider, (0.15, 0.60))
        ph = pm * 0.02
    if hit or miss:
        in_cost = (miss / 1e6 * pm) + (hit / 1e6 * ph)
    else:
        in_cost = pt / 1e6 * pm
    return in_cost + (ct / 1e6 * po)


def _extract_usage(body: dict) -> dict:
    u = body.get("usage") or {}
    pt = int(u.get("prompt_tokens", 0) or 0)
    # Caché: DeepSeek usa `prompt_cache_hit_tokens`; GLM/z.ai usa
    # `prompt_tokens_details.cached_tokens`. Soportamos AMBOS.
    hit = int(u.get("prompt_cache_hit_tokens", 0) or 0)
    if not hit:
        det = u.get("prompt_tokens_details") or {}
        hit = int(det.get("cached_tokens", 0) or 0)
    miss = int(u.get("prompt_cache_miss_tokens", 0) or 0)
    if not miss and pt:
        miss = max(0, pt - hit)
    return {
        "prompt_tokens": pt,
        "completion_tokens": int(u.get("completion_tokens", 0) or 0),
        "total_tokens": int(u.get("total_tokens", 0) or 0),
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_miss_tokens": miss,
    }


def chat_verbose(messages, provider: Optional[str] = None,
                 temperature: float = 0.4, max_tokens: int = 1200,
                 model: Optional[str] = None,
                 reasoning_effort: Optional[str] = None,
                 thinking: Optional[str] = None,
                 base_url: Optional[str] = None,
                 api_key: Optional[str] = None,
                 price_provider: Optional[str] = None,
                 tools: Optional[list] = None,
                 tool_choice: Optional[str] = None):
    """Envía y devuelve (texto, meta) con meta = {usage, cost, model, provider}.

    model / reasoning_effort / thinking / base_url / api_key permiten usar OTRO
    proveedor o modelo (p. ej. el revisor BigBoss con Kimi). price_provider indica
    qué tarifa aplicar al coste.
    """
    cfg = resolve_provider_cfg(provider)
    key = api_key or cfg["api_key"]
    if not key:
        raise RuntimeError(
            "No hay TW_LLM_API_KEY (ni DEEPSEEK_API_KEY/ZAI_API_KEY). "
            "Configúrala en el .env del agente.")

    base = base_url or cfg["base_url"]
    mdl = model or cfg["model"]
    payload = {
        "model": mdl,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # Tool calling NATIVO (function calling OpenAI-compatible): el modelo devuelve
    # `tool_calls` estructurados en vez de emitir su formato de texto nativo.
    if tools:
        payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
    # Kimi (Moonshot) es thinking-only: fija temperature a 1 y no admite otros valores.
    if "moonshot.ai" in base:
        payload["temperature"] = 1
    else:
        payload["temperature"] = temperature
    # DeepSeek V4: thinking + reasoning_effort (low/medium/high). Solo para DeepSeek.
    # thinking='0' -> desactivar razonamiento: NO mandar reasoning_effort
    # (evita que genere reasoning_content y gaste el presupuesto de max_tokens).
    if cfg["provider"] == "deepseek" and "deepseek.com" in base:
        eff = reasoning_effort if reasoning_effort is not None else _get("TW_REASONING_EFFORT", "").strip().lower()
        thk = thinking if thinking is not None else _get("TW_THINKING", "").strip()
        if thk == "0":
            payload["thinking"] = {"type": "disabled"}
        elif eff in ("low", "medium", "high"):
            payload["reasoning_effort"] = eff
            payload["thinking"] = {"type": "enabled"}
    # GLM (z.ai): thinking + reasoning_effort (low|high|max). OJO: GLM-5.3 SIEMPRE
    # razona y NO admite desactivarlo (thinking disabled daría HTTP 400), así que si
    # piden thinking=0 simplemente NO enviamos el bloque (queda en su modo normal).
    if cfg["provider"] in ("glm", "zai") or "z.ai" in base:
        thk = thinking if thinking is not None else _get("TW_THINKING", "").strip()
        eff = (reasoning_effort if reasoning_effort is not None
               else _get("TW_REASONING_EFFORT", "").strip().lower())
        if thk != "0":
            payload["thinking"] = {"type": "enabled"}
            if eff in ("low", "high", "max"):
                payload["reasoning_effort"] = eff
    # z.ai (GLM 5.3-Flash) es thinking-only: NO acepta thinking:{disabled}; el
    # control se hace SOLO con reasoning_effort (low/high/max; 'medium' NO existe
    # y daría error 1210, así que se sube a 'high'). 'thinking':'0' se ignora.
    if cfg["provider"] == "glm" and "z.ai" in base:
        eff = reasoning_effort if reasoning_effort is not None else _get("TW_REASONING_EFFORT", "").strip().lower()
        if eff == "medium":
            eff = "high"
        if eff in ("low", "high", "max"):
            payload["reasoning_effort"] = eff
    t0 = time.time()          # mide el tiempo de esta llamada (para el pie por rol)
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}
    # Reintentos para fallos TRANSITORIOS de red (p. ej. http.client.IncompleteRead
    # cuando la API corta la conexión a media respuesta). NO reintenta errores HTTP
    # con código (4xx/5xx), que son deterministas.
    body = None
    _last = None
    for _try in range(3):
        req = urllib.request.Request(base, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"HTTP {e.code} de {cfg['provider']}: {detail}") from e
        except Exception as e:  # noqa: BLE001  (IncompleteRead, URLError, timeout…)
            _last = e
            if _try < 2:
                time.sleep(1.5 * (_try + 1))
                continue
            raise RuntimeError(
                f"falló la llamada a {cfg['provider']} tras 3 intentos: {e}") from e

    try:
        msg = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Respuesta inesperada de {cfg['provider']}: {body}") from exc
    content = (msg.get("content") or "").strip()
    tool_calls = msg.get("tool_calls") or None
    # Modelos "thinking-only" (Kimi K2.7 Code) pueden responder solo en
    # reasoning_content si el presupuesto se agotó antes de la respuesta final.
    if not content and not tool_calls:
        content = (msg.get("reasoning_content") or "").strip()
    if not content and not tool_calls:
        raise RuntimeError(
            f"Respuesta vacía de {cfg['provider']} (finish={body['choices'][0].get('finish_reason')})")

    usage = _extract_usage(body)
    price_prov = price_provider or cfg["provider"]
    meta = {
        "usage": usage,
        "cost": _estimate_cost(price_prov, usage, mdl),
        "elapsed": round(time.time() - t0, 3),
        "model": mdl,
        "provider": cfg["provider"],
        "tool_calls": tool_calls,
        "finish": body["choices"][0].get("finish_reason"),
    }
    if _recording:
        _record.append({
            "ts": None,
            "provider": cfg["provider"], "model": mdl,
            "messages": json.loads(json.dumps(messages)),  # snapshot
            "response": content,
            "meta": meta,
        })
    return content, meta


def chat(messages, provider: Optional[str] = None,
         temperature: float = 0.4, max_tokens: int = 1200) -> str:
    """Wrapper simple: solo devuelve el texto (para compatibilidad)."""
    text, _ = chat_verbose(messages, provider=provider, temperature=temperature,
                           max_tokens=max_tokens)
    return text

