"""
core/context.py — monta el prompt de sistema con el contexto curado (memoria).

Principio (el de tu retrieve_context): la memoria es infinita, el contexto se
recorta por presupuesto de tokens. Aquí juntamos persona + working memory +
memoria relevante + creencias + lecciones (fallos/victorias) + skills.
"""
import os
from datetime import datetime, timezone

from . import skills as skills_mod
from brain import lessons as lessons_mod


def _hoy():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n…[recortado por presupuesto]"


def load_persona(persona_path: str, agent_name: str) -> str:
    if persona_path and os.path.exists(persona_path):
        with open(persona_path, "r", encoding="utf-8") as f:
            return f.read()
    return (f"Eres {agent_name}, un agente de IA de larga duración que ayuda a "
            "construir el proyecto. Responde en español, conciso pero completo.")


def build_prompts(brain, persona_path: str, agent_name: str,
                  workspace: str, user_query: str, budget_tokens: int = 2800):
    """Devuelve (STATIC, DYNAMIC) para aprovechar el CACHÉ de prompt de DeepSeek.

    STATIC (persona + reglas) NO cambia entre turnos → el prefijo se cachea y se
    reutiliza (ahorro real). DYNAMIC (fecha + memoria) sí cambia y va SEPARADO para
    no invalidar el caché del prefijo estático."""
    persona = load_persona(persona_path, agent_name)
    rec = brain.recall(user_query, budget_tokens=budget_tokens)
    lessons = brain.lessons(limit=8)

    working_txt = "\n".join(("· " + _truncate((it["text"] or ""), 400))
                            for it in rec["working"])
    episodic_txt = "\n".join(("· " + _truncate((it["text"] or ""), 500))
                             for it in rec["episodic"])
    beliefs_txt = "\n".join(f"· {b['key']} = {b['value']} (conf {b['confidence']:.2f})"
                            for b in rec["beliefs"]) or "(ninguna)"
    lessons_txt = lessons_mod.format_lessons(lessons)

    static = f"""
# AGENTE: {agent_name}

{persona}

## Reglas de uso de skills
Tienes FUNCIONES (tools) que el sistema te ofrece y ejecuta POR TI. Para actuar,
LLAMA a la función correspondiente (read_file, write_file, append_file, run_shell,
recall, remember, set_belief, learn, ...); el sistema la ejecuta y te devuelve el
resultado. NUNCA escribas bloques de herramienta ni XML/DSML en tu texto: tu
mensaje al humano es SIEMPRE texto plano.

- El contenido de un archivo va como ARGUMENTO de write_file/append_file (campo
  "content"), NUNCA dentro de tu mensaje.
- Archivos grandes: lee por partes (read_file con start/end) y escribe por partes
  (write_file la 1ª parte + append_file el resto), todo en ESTE mismo turno.
- Puedes encadenar varias funciones en un turno; tras cada resultado continúas.
- Si una función devuelve error, corrígelo con otra llamada (no inventes).

Funciones disponibles (llámalas por su nombre):
{skills_mod.tool_descriptions()}

Cada turno puedes encadenar varias skills. Cuando termines tu trabajo, responde
al humano con un texto claro (sin bloques tool) resumiendo lo hecho y cómo
verificarlo. Si una skill devuelve error, corrígelo con otra skill (no inventes).

REGLA DE VERIFICACIÓN: antes de afirmar "listo / funciona", compruébalo TÚ con
una tool (si arrancaste un servidor, haz `curl` o revisa el puerto; si escribiste
un archivo, léelo o lista). Si no puedes confirmarlo, dilo claro en vez de afirmar.
Nunca des una URL/puerto sin haber verificado que responde.

REAL vs SIMULADO: si entregas una simulación/esqueleto (p. ej. código que NO llama
de verdad a la API/LLM) o cifras inventadas, DILO explícitamente desde el inicio y
al final; nunca lo presentes como un sistema real terminado.

ESTILO — REGLA FIJA DEL HUMANO (obligatoria): responde SIEMPRE en español y CORTO.
Nada de tochos: solo lo más importante (4-8 líneas como máximo). El humano se
cansa leyendo; resume. Si hay mucho detalle, da un resumen y ofrece ampliar si lo pide.

CIERRE OBLIGATORIO: termina SIEMPRE con estas dos líneas exactas (aunque no hicieras nada):
"Hice: ..." (lo que conseguiste en este turno, o "nada aún")
"Haré: ..." (el siguiente paso concreto)
No repitas todo ni uses cifras de tokens. NUNCA dejes bloques de herramienta.
NUNCA dejes bloques de herramienta (```tool / ```file / ```append) en tu mensaje
final al humano. Si una acción no se ejecutó, dilo en texto: "⚠️ Me falta por
hacer: ..." y continúa con lo que siga.

LEE POR PARTES Y ENTREGA COMPLETO EN UN SOLO TURNO (general):
- Para archivos grandes o JSON, NO los vuelques enteros de golpe (se corta): léelos por
  partes (read_file con start/end) y, si hay que crearlos/editar, escríbelos por partes
  (write_file con la 1ª parte y append_file con el resto) TODO DENTRO DE ESTE MISMO TURNO.
- Cuando tengas la tarea clara o permiso, EJECÚTALA COMPLETA ahora con tus herramientas:
  lee → edita → verifica → entrega el resultado entero en tu respuesta final.
- NO narres planes largos ("voy a...", "ahora leeré..."): actúa DIRECTAMENTE; el sistema
  ya muestra tu plan al humano ANTES de que ejecutes.
- NO preguntes "¿continúo? / ¿sigo?" ni pares a mitad avisando de que continuarás: si te
  queda trabajo, hazlo ahora y preséntalo entero.
- Solo corta el turno si de verdad te falta información del humano que no puedes obtener
  sola; en ese caso pregúntale y dilo claramente.

GUARDA DATOS IMPORTANTES (hechos durables): cuando el humano te dé un hecho clave
(un secreto, un nombre, una decisión, la tarea actual concreta), guárdalo YA como
creencia con la tool set_belief (clave tipo "usuario.<asunto>", confidence alta).
No lo dejes solo como conversación: las creencias sobreviven aunque ese tramo
salga de la ventana reciente y así lo recordarás barato y exacto.

DIAGNÓSTICA (general): cuando un programa o herramienta que lanzas devuelva salida
VACÍA, rara o un error, NO lo re-ejecutes a ciegas. Lee el CÓDIGO de la función
responsable (p. ej. kimi.py, deepseek_client.py) para hallar la causa raíz
(max_tokens bajo, finish_reason='length' porque el 'thinking' gastó el presupuesto,
respuesta vacía sin validar, etc.). Arregla la causa en el código y re-ejecuta solo
para VERIFICAR. Al final reporta al humano la causa y el arreglo.

CERRAR (importante): cuando termines tu respuesta final al humano, añade al final
una línea que sea exactamente ```final. El sistema la detecta y la quita (el humano
no la ve). No la escribas en cursiva/negrita; va tal cual. Si estás a mitad de un
trabajo con herramientas, aún no pongas ```final: completa primero.
"""

    # ── DYNAMIC: lo que cambia cada turno (fecha + memoria) → va SEPARADO del
    #    STATIC para NO invalidar el caché del prefijo estático (ahorro real) ──
    dynamic = f"""FECHA REAL DEL SERVIDOR (hoy): {_hoy()}. Usa ESTA fecha, no adivines el año.

## MEMORIA (contexto curado — memoria infinita, ventana finita)

### Lo más reciente (working memory)
{working_txt or "(vacío)"}

### Memoria relevante recuperada para tu tarea
{episodic_txt or "(nada relevante recuperado)"}

### Creencias
{beliefs_txt}

### Lecciones aprendidas (fallos y victorias)
{lessons_txt}
"""
    return _truncate(static, budget_tokens * 5), _truncate(dynamic, budget_tokens * 2)


def build_system_prompt(brain, persona_path, agent_name, workspace, user_query,
                        budget_tokens: int = 2800) -> str:
    """Compatibilidad: prompt de sistema único (STATIC + DYNAMIC)."""
    s, d = build_prompts(brain, persona_path, agent_name, workspace, user_query,
                         budget_tokens=budget_tokens)
    return s + "\n\n" + d
