"""
turno_grupo.py — UN turno de un agente en MODO GRUPO (no toca run.py).

Lo usa el coordinador (core/coordinador.py) para "despertar" a un bot cuando
otro bot (o el humano) lo menciona en la pizarra. Telegram NO entrega a un bot
los mensajes de otro bot, asi que el coordinador le pasa el prompt ya montado
con el contexto de la pizarra y este script:

  1) carga la config del agente (configAgentes.json) -> entorno TW_*
  2) construye el Agent (reutiliza la clase de run.py, sin ejecutar su main)
  3) ejecuta un turno (run_turn) con las herramientas reales
  4) manda la respuesta al GRUPO con el token de ese bot (publicar=True ->
     queda tambien en la pizarra para el otro bot)

Seguridad: fuerza TW_CONFIRM_EDITS=1, de modo que cualquier ESCRITURA del bot
queda EN PAUSA esperando el "si/no" del humano (protocolo pedido). En la fase de
conversacion el prompt ademas le pide NO tocar nada.

Uso interno:
  python3 turno_grupo.py --agent jokerv2 --chat -100... --prompt-file /tmp/p.txt
Devuelve por stdout una linea JSON con {ok, agente, chars, stats}.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Reutilizamos el runtime real de run.py (importar NO ejecuta su main()).
import run as runmod  # noqa: E402


def con_ping(texto, ping, es_reparto=False):
    """GARANTIZA que el mensaje DIRIJA a su compañero.

    Dos motivos:
      1) VISIBILIDAD: el humano veía dos informes sueltos al jefe en vez de una
         conversación (el relevo ocurría solo por dentro, con '🔁').
      2) La pizarra lo despierta aunque el bot se olvide de mencionarlo.
    """
    if not ping:
        return texto
    if ("@" + str(ping).lower()) in (texto or "").lower():
        return texto                      # ya lo menciona: no tocamos nada
    cierre = (f"\n\n@{ping}, ¿confirmas el reparto o lo ajustamos?"
              if es_reparto else f"\n\n@{ping}, ¿tú cómo lo ves?")
    return (texto or "").rstrip() + cierre


def main():
    ap = argparse.ArgumentParser(description="Un turno de agente en modo grupo")
    ap.add_argument("--agent", required=True, help="id en configAgentes.json")
    ap.add_argument("--chat", required=True, help="chat_id del grupo (negativo)")
    ap.add_argument("--prompt-file", required=True, help="fichero con el prompt")
    ap.add_argument("--max-iters", type=int, default=None)
    ap.add_argument("--ping", default="",
                    help="@usuario al que GARANTIZAR el relevo: si el bot no lo "
                         "menciona, se añade la mención (así el humano VE el relevo)")
    args = ap.parse_args()

    with open(args.prompt_file, "r", encoding="utf-8") as fh:
        prompt = fh.read()

    # 1) config del agente -> entorno
    runmod.agentconfig.bootstrap(args.agent, verbose=False)

    # 2) Seguridad POR FASE:
    #    · FASE 1 (conversación): escrituras EN PAUSA (no toca nada todavía).
    #    · FASE 2 (ejecución, el humano YA confirmó): escrituras PERMITIDAS → así
    #      ejecuta de verdad (tipo Cline) en vez de quedarse pidiendo permiso en bucle.
    _fase2 = "FASE 2" in prompt
    os.environ["TW_CONFIRM_EDITS"] = "0" if _fase2 else "1"

    agent = runmod.Agent()
    out, stats = "", None
    err = ""
    token = runmod.cfg("TW_TELEGRAM_BOT_TOKEN")
    tb = None
    if token:
        try:
            from channels.telegram import TelegramBot
            tb = TelegramBot(token)
        except Exception as e:  # noqa: BLE001
            err = f"telegram no disponible: {e}"

    # ── AVISO EFÍMERO (se edita y se BORRA solo al terminar) ────────────────
    # Antes el grupo se quedaba MUDO durante el relevo: el humano no veía nada
    # mientras el otro bot trabajaba y solo aparecía la respuesta final, como
    # caída del cielo. Ahora se muestra QUIÉN trabaja, EN QUÉ MODO y QUÉ hace
    # (mismo estilo que el chat privado de Joker).
    t0 = time.time()
    etiqueta = f"@{tb.username}" if (tb and tb.username) else args.agent
    modo = "✍️ en EJECUCIÓN" if _fase2 else "💬 conversando"
    ef = {"mid": None}

    def _pinta(txt):
        if tb is None:
            return
        try:
            if ef["mid"] is None:
                ef["mid"] = tb.send(args.chat, txt)
            else:
                tb.edit(args.chat, ef["mid"], txt)
        except Exception:  # noqa: BLE001
            pass

    def _cabecera(extra=""):
        seg = int(time.time() - t0)
        cab = f"🎬 {etiqueta} ({agent.name}) · {modo} · ⏱ {seg}s"
        return cab + (f"\n\n{extra}" if extra else "")

    def _hook(name, a, result):
        d = runmod._rich_desc(name, a)      # ya trae el EMOJI de la herramienta
        if result is None:
            _pinta(_cabecera(f"{d}…"))
        else:
            prev = runmod._rich_result(result)
            _pinta(_cabecera(f"{d}\n\n{prev}" if prev else f"{d} ✓"))

    agent.tool_hook = _hook
    if tb is not None:
        try:
            tb.send_action(args.chat, "typing")
        except Exception:  # noqa: BLE001
            pass
        _pinta(_cabecera("🧠 pensando…"))

    try:
        out, stats = agent.run_turn(prompt, max_iters=args.max_iters,
                                    ctx={"chat_id": str(args.chat)})
    except Exception as e:  # noqa: BLE001
        import traceback
        err = traceback.format_exc()[:1500]
        out = f"⚠️ {agent.name}: error en el turno ({e})"
    finally:
        agent.tool_hook = None
        if ef["mid"] is not None and tb is not None:
            try:
                tb.delete(args.chat, ef["mid"])   # fuera el efímero
            except Exception:  # noqa: BLE001
                pass

    footer = ""
    try:
        if stats:
            footer = runmod.format_summary(stats, as_footer=True)
    except Exception:  # noqa: BLE001
        footer = ""

    # Limpiar lo INTERNO antes de publicar (evita contaminar la pizarra y bucles):
    #  · el PLAN de la Fase 0 (🧭 Plan: ... ———) NO debe ir al tablón
    #  · el marcador ```final
    #  · el pie de tokens (métricas que el otro bot leería sin aportar nada)
    import re as _re
    limpio = out or ""
    limpio = _re.sub(r"^\s*🧭\s*Plan:.*?\n\s*———\s*\n", "", limpio, flags=_re.DOTALL)
    limpio = _re.sub(r"```final\s*$", "", limpio).strip()
    # ── LLAMADA A HERRAMIENTA ESCRITA COMO TEXTO (le pasa a GLM) ─────────────
    # El modelo a veces pone el comando en el MENSAJE en vez de ejecutarlo (se vio
    # en el grupo: "```run_shell cp fw_stt.py ...```" publicado como texto y el
    # backup NUNCA se hizo, pero el bot creía que sí). Aquí se DETECTA, se quita del
    # mensaje (no se ensucia el grupo) y se AVISA por stdout para que quede claro
    # que NO se ejecutó (el coordinador lo muestra en su log).
    _pats = [
        r"```(?:run_shell|bash|sh|shell|file|append|edit)\b.*?```",
        r"<invoke\b.*?</invoke>",
        # Variante que se coló en el grupo: bloque JSON con los ARGUMENTOS de la tool
        # ("run_shell:\n\n```json {\"command\": ...}```"), con cualquier etiqueta.
        r"```[a-zA-Z]*\s*\{[^{}]*\"(?:command|path|content|find|pattern|query|texto)\""
        r"\s*:[^{}]*\}```",
    ]
    _bloques = []
    for _p in _pats:
        _bloques += _re.findall(_p, limpio, flags=_re.DOTALL)
    if _bloques:
        for _p in _pats:
            limpio = _re.sub(_p, "", limpio, flags=_re.DOTALL)
        # rótulo suelto que queda delante ("run_shell:")
        limpio = _re.sub(r"(?m)^\s*(?:run_shell|bash|shell|edit_file|write_file)\s*:\s*$",
                         "", limpio).strip()
        print("[turno_grupo] ⚠️ NO EJECUTADO: el bot escribió la herramienta como "
              f"TEXTO ({len(_bloques)} bloque(s)): {_bloques[0][:120]!r}", flush=True)
    # RELEVO VISIBLE: si el bot NO mencionó a su compañero, se añade la mención.
    # Así el humano VE que es una conversación (y no dos informes sueltos) y la
    # pizarra lo despierta igualmente aunque el bot se olvide.
    limpio = con_ping(limpio, args.ping, es_reparto=("REPARTO" in prompt))
    if not limpio:
        limpio = "(sin mensaje)"

    if tb is not None:
        try:
            # AL GRUPO va la respuesta + PIE DE USO (⬆️ in / ⬇️ out / 📀 caché /
            # 💰 coste), igual que en el chat normal. A la PIZARRA no llega el pie:
            # channels/telegram.py ya lo limpia antes de publicar (el otro bot no
            # necesita métricas). `footer` se calculaba y se tiraba a la basura
            # → ESE era el motivo de que los turnos de grupo salieran sin gasto.
            tb.send(args.chat, limpio + (footer or ""), publicar=True)
        except Exception as e:  # noqa: BLE001
            err = err or f"envio telegram fallo: {e}"
    try:
        agent.close()
    except Exception:  # noqa: BLE001
        pass

    print(json.dumps({"ok": not err, "agente": args.agent,
                      "chars": len(out or ""), "err": err[:300],
                      "ts": time.time()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
