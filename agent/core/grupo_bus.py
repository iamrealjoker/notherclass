"""
core/grupo_bus.py — PIZARRA COMPARTIDA del grupo (blackboard entre agentes).

PROBLEMA QUE RESUELVE
---------------------
La Bot API de Telegram NO entrega a un bot los mensajes que escribe OTRO bot.
Asi que si JokerV1 responde en el grupo, JokerV2 nunca lo "ve" por getUpdates.
Sin embargo los dos comparten el mismo workspace.

SOLUCION
--------
Cada mensaje relevante del grupo (lo que dice el humano y lo que responde cada
bot) se apunta en un fichero JSONL por chat: grupo_bus/chat_<id>.jsonl.
Cuando a un bot lo mencionan, ademas del texto nuevo recibe como CONTEXTO los
ultimos mensajes que apuntaron los demas. Asi "lee" la respuesta del otro bot
aunque Telegram no se la entregue.

Es write-only-append + lectura con dedupe por message_id de Telegram (para no
duplicar el mismo mensaje del humano cuando los dos bots lo reciben).
"""
import json
import os
import time

# .../agent/core/grupo_bus.py -> repo root = /app/notherclass
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_BUS_DIR = os.path.join(_ROOT, "grupo_bus")


def _path(chat_id, bus_dir=None):
    d = bus_dir or _BUS_DIR
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"chat_{int(chat_id)}.jsonl")


def publicar(chat_id, autor, texto, msg_id=None, para=None, bus_dir=None):
    """Apunta un mensaje en la pizarra. Dedupe por msg_id (Telegram lo entrega
    a cada bot mencionado; queremos UNA sola entrada). Devuelve True si se
    anadio, False si era duplicado."""
    texto = (texto or "").strip()
    if not texto:
        return False
    p = _path(chat_id, bus_dir)
    if msg_id is not None:
        for e in _leer(chat_id, 200, bus_dir):
            if str(e.get("msg_id")) == str(msg_id):
                return False  # ya estaba apuntado por el otro bot
    rec = {"ts": time.time(), "msg_id": msg_id, "autor": autor,
           "para": para, "texto": texto}
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def _leer(chat_id, n=50, bus_dir=None):
    p = _path(chat_id, bus_dir)
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
            if not isinstance(e, dict):
                continue
            # esquemas alternativos: {'from','text'} -> {'autor','texto'}
            if not e.get("autor") and e.get("from"):
                e["autor"] = e.get("from")
            if not e.get("texto") and e.get("text"):
                e["texto"] = e.get("text")
            out.append(e)
    return out[-n:]


def ultimos(chat_id, n=12, excluir_autor=None, bus_dir=None):
    """Ultimos n mensajes, opcionalmente excluyendo los del propio autor."""
    items = _leer(chat_id, max(n * 3, 30), bus_dir)
    if excluir_autor:
        items = [e for e in items if e.get("autor") != excluir_autor]
    return items[-n:]


def formatear_contexto(chat_id, mi_autor, n=12, bus_dir=None):
    """Texto listo para inyectar al prompt: lo que han dicho los DEMAS."""
    items = ultimos(chat_id, n=n, excluir_autor=mi_autor, bus_dir=bus_dir)
    if not items:
        return ""
    lineas = []
    for e in items:
        autor = e.get("autor") or "?"
        txt = (e.get("texto") or "").replace("\n", " ").strip()
        if len(txt) > 700:
            txt = txt[:700] + "…"
        lineas.append(f"· {autor}: {txt}")
    return ("[Contexto compartido del grupo — lo ultimo que dijeron los demas "
            "(llegado por la pizarra, NO por Telegram)]\n"
            + "\n".join(lineas)
            + "\n[Fin del contexto]\n\n")
