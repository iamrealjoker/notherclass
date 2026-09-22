"""
brain/consolidate.py — APRENDER DE VERDAD (bruto → neto).

El problema que detectaste en tu docker: si solo acumulas conversaciones,
"recoges datos" pero no aprendes. Aquí, cada X tiempo (cron) tomamos los chunks
BRUTOS sin procesar, se los pasamos a un LLM y nos devuelve SOLO el aprendizaje
NETO (lo que de verdad vale la pena recordar):

  - creencias (beliefs) que se consolidan / se contradicen / se crean,
  - lecciones de tipo win/fail,
  - un resumen de decisiones y estado.

Lo bruto se marca como procesado (no se re-procesa, pero no se borra → sigue
disponible para recall vectorial). Así la memoria crece en CALIDAD, no en ruido.
"""
import json
import re

_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM = """Eres el "sueño" (consolidación) de un agente de IA. Recibes un volcado
BRUTO de memoria (conversaciones, acciones, resultados). Tu tarea: destilarlo a
aprendizaje NETO, eliminando el ruido.

Devuelve SOLO un objeto JSON con esta forma exacta (sin markdown, sin texto extra):
{
  "summary": "1-2 frases con las decisiones/estado que importan ahora",
  "beliefs": [{"key": "dominio.aspecto", "value": "afirmación concisa", "confidence": 0.0}],
  "lessons": [{"kind": "win", "title": "...", "body": "...", "outcome": "..."}]
}
Reglas:
- belief.confidence entre 0 y 1. Si un belief nuevo contradice uno viejo de baja
  confianza, indícalo igual (el sistema lo sobreescribe).
- lessons.kind SOLO "win" o "fail". Solo si hay un patrón o lección transferible.
- Si no hay nada importante, devuelve summary vacío y arrays vacíos.
- No inventes: solo destila lo que está en el volcado."""


def _extract_json(text: str):
    m = _JSON_OBJ.search(text or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _digest(raw: list) -> str:
    lines = []
    for i, r in enumerate(raw):
        txt = (r["text"] or "").strip().replace("\n", " ")
        lines.append(f"[{i}] ({r.get('kind')}) {txt[:400]}")
    return "\n".join(lines)


def consolidate(brain, chat_fn, agent_name: str = "Forja",
                persona_hint: str = "construir NotherClass",
                min_chunks: int = 3, max_chars: int = 16000) -> dict:
    """Destila chunks brutos a aprendizaje neto. Devuelve métricas.

    chat_fn: callable(messages:list) -> str  (debe enviar al LLM y devolver texto).
    """
    raw = brain.unprocessed_chunks(limit=200, max_chars=max_chars)
    if len(raw) < min_chunks:
        return {"skipped": True, "reason": "pocos chunks sin procesar",
                "raw_read": len(raw)}

    digest = _digest(raw)
    user_msg = (f"Agente: {agent_name} (objetivo: {persona_hint}).\n"
                f"\nVolcado BRUTO de memoria ({len(raw)} fragmentos):\n{digest}\n"
                "\nDevuelve el JSON de aprendizaje NETO.")

    try:
        text = chat_fn([{"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": user_msg}])
    except Exception as e:
        return {"error": str(e), "raw_read": len(raw)}

    # Si el LLM devolvió VACÍO (p. ej. gastó max_tokens en 'thinking'), NO marcamos
    # los brutos como procesados: así se reintentarán y no se pierde el aprendizaje.
    if not (text and text.strip()):
        return {"error": "LLM devolvió vacío (revisar max_tokens/thinking)",
                "raw_read": len(raw)}

    parsed = _extract_json(text)
    summary = ""
    beliefs = []
    lessons = []
    if isinstance(parsed, dict):
        summary = str(parsed.get("summary") or "").strip()
        beliefs = parsed.get("beliefs") or []
        lessons = parsed.get("lessons") or []
        if not isinstance(beliefs, list):
            beliefs = []
        if not isinstance(lessons, list):
            lessons = []
    elif text and text.strip():
        # si el LLM no siguió el JSON, tratamos todo como resumen neto
        summary = text.strip()

    applied_beliefs = 0
    applied_lessons = 0
    for b in beliefs[:20]:
        try:
            key = str(b.get("key") or "").strip()
            val = str(b.get("value") or "").strip()
            conf = float(b.get("confidence", 0.6) or 0.6)
            if key and val:
                brain.set_belief(key, val, confidence=max(0.0, min(1.0, conf)),
                                 source="consolidacion")
                applied_beliefs += 1
        except Exception:
            continue
    for l in lessons[:20]:
        try:
            kind = str(l.get("kind") or "").strip().lower()
            if kind not in ("win", "fail"):
                kind = "fail" if "fail" in str(l) else "win"
            brain.learn(kind, str(l.get("title") or "Lección destilada"),
                        str(l.get("body") or ""), str(l.get("outcome") or ""))
            applied_lessons += 1
        except Exception:
            continue

    raw_ids = [r["id"] for r in raw]
    brain.mark_processed(raw_ids)
    if summary:
        brain.add_summary_chunk("Consolidación: " + summary, meta={"kind": "summary"})

    return {
        "skipped": False,
        "raw_read": len(raw),
        "discarded": len(raw_ids),
        "summary_saved": bool(summary),
        "beliefs_applied": applied_beliefs,
        "lessons_applied": applied_lessons,
    }
