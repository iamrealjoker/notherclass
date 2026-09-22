"""
brain/lessons.py — compone las lecciones (fallos y victorias) para el contexto,
y ofrece un helper para registrar retroalimentación cuando el humano marca un
resultado como bien/mal (aprendizaje explícito).
"""
from typing import List, Optional

from .store import Brain


def format_lessons(lessons: List[dict], max_lessons: int = 8) -> str:
    """Serializa lecciones recientes para inyectarlas al prompt del agente."""
    if not lessons:
        return "(todavía no hay lecciones registradas)"
    lines = []
    for l in lessons[:max_lessons]:
        kind = "✅ VICTORIA" if l.get("kind") == "win" else "❌ FALLO"
        lines.append(f"- [{kind}] {l.get('title')}: {l.get('body')}")
        if l.get("outcome"):
            lines.append(f"    → {l.get('outcome')}")
    return "\n".join(lines)


def feedback_to_lesson(brain: Brain, ok: bool, user_text: str) -> str:
    """El humano confirma si una acción salió bien o mal → se guarda como lección."""
    kind = "win" if ok else "fail"
    if ok:
        title = "Acción confirmada como correcta"
        body = f"El humano validó este resultado: {user_text[:300]}"
    else:
        title = "Acción marcada como fallida"
        body = f"El humano reportó un problema: {user_text[:300]}"
    return brain.learn(kind, title, body)
