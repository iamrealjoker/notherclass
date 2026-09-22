"""
channels/telegram.py — puente Telegram mínimo (long-polling con stdlib).

Habla con el agente desde tu móvil. No requiere librerías externas: usa la Bot
API de Telegram con urllib. El daemon (run.py) registra un callback on_message.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# pizarra compartida entre agentes (mismo workspace). Import tolerante.
try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _CORE = os.path.join(os.path.dirname(_HERE), "core")
    if _CORE not in sys.path:
        sys.path.insert(0, _CORE)
    import grupo_bus  # type: ignore
except Exception:  # noqa: BLE001
    grupo_bus = None

API = "https://api.telegram.org/bot{token}/{method}"
FILE_API = "https://api.telegram.org/file/bot{token}/{path}"


class TelegramBot:
    def __init__(self, token: str, on_message=None, on_voice=None, allowed_ids=None):
        self.token = token
        self.on_message = on_message or (lambda chat_id, text: None)
        self.on_voice = on_voice or (lambda chat_id, file_id: None)
        self.allowed = set()
        if allowed_ids:
            for x in str(allowed_ids).split(","):
                x = x.strip()
                if x.isdigit():
                    self.allowed.add(int(x))
        self._offset = 0
        # ── Modo SIN Telegram (para DEV conviviendo con PROD) ──────────────────
        # TW_TELEGRAM_DISABLED=1 -> no hacemos polling ni getMe ni enviamos. El
        # proceso sigue vivo con la web/supervisor. Evita el 409 "Conflict" que
        # ocurre si dos contenedores hacen long-polling con el MISMO token.
        self.disabled = str(os.environ.get("TW_TELEGRAM_DISABLED", "")).strip().lower() in ("1", "true", "yes", "on")
        # identidad del bot (para el filtro por @mencion en grupos)
        self.username = None
        self.bot_id = None
        if self.disabled:
            print("[telegram] 🔇 desactivado por TW_TELEGRAM_DISABLED (sin polling).", flush=True)
            return
        try:
            r = (self._call("getMe", {}) or {}).get("result") or {}
            self.username = r.get("username")
            self.bot_id = r.get("id")
        except Exception:  # noqa: BLE001
            pass

    # ── helpers ────────────────────────────────────────────────────────────
    def _call(self, method: str, params: dict) -> dict:
        url = API.format(token=self.token, method=method)
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=70) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def send(self, chat_id, text: str, publicar: bool = False):
        # En modo sin Telegram no enviamos NADA (evita que dev escriba a chats reales).
        if self.disabled:
            return None
        # Telegram limita ~4096 chars por mensaje; troceamos.
        text = (text or "").strip() or "…"
        last_mid = None
        tg_err = ""
        for i in range(0, len(text), 3800):
            chunk = text[i:i + 3800]
            # Reintento con backoff ante 429 (Too Many Requests): si nos
            # rendimos al primer 429, el mensaje se pierde y el grupo se queda
            # mudo. Damos varias oportunidades antes de darlo por fallido.
            for intento in range(4):
                try:
                    res = self._call("sendMessage",
                                     {"chat_id": chat_id, "text": chunk,
                                      "disable_web_page_preview": True})
                    try:
                        last_mid = res["result"]["message_id"]
                    except Exception:
                        pass
                    tg_err = ""
                    break
                except Exception as e:  # noqa: BLE001
                    tg_err = str(e)
                    if "429" in tg_err and intento < 3:
                        time.sleep(2.5 * (intento + 1))
                        continue
                    break
        # CLAVE: apuntamos SIEMPRE la respuesta en la pizarra (fuente de verdad
        # del coordinador), aunque Telegram haya fallado. Antes el publicar
        # estaba DESPUÉS del envío y un 429 lo saltaba -> la ronda se colgaba.
        if publicar and grupo_bus is not None and str(chat_id).startswith("-"):
            try:
                # Limpiar lo INTERNO antes de la pizarra: el PLAN (🧭) no es un
                # mensaje al grupo; filtrarlo hacía que el otro bot lo leyera como
                # si fuera un mensaje y entrara en BUCLE. Fuera también ```final
                # y el pie de tokens (métricas que el otro bot no necesita).
                import re as _re
                _t = text or ""
                _t = _re.sub(r"^\s*🧭\s*Plan:.*?\n\s*———\s*\n", "", _t, flags=_re.DOTALL)
                _t = _re.sub(r"```final\s*$", "", _t)
                _t = _re.split(r"\n\s*---\n\s*(?:🔧 herramientas:|🃏 )", _t)[0].strip()
                grupo_bus.publicar(chat_id, "@" + (self.username or "bot"),
                                   _t or "(sin mensaje)")
            except Exception:  # noqa: BLE001
                pass
        if tg_err:
            print(f"[telegram] ⚠️ send falló (pero quedó en la pizarra): {tg_err}",
                  flush=True)
        return last_mid

    def edit(self, chat_id, message_id, text: str):
        """Reemplaza un mensaje previo (mensaje 'temporal' que se actualiza)."""
        if message_id is None:
            return None
        try:
            self._call("editMessageText",
                       {"chat_id": chat_id, "message_id": message_id,
                        "text": (text or "")[:3800]})
        except Exception:
            pass
        return message_id

    def delete(self, chat_id, message_id):
        """Borra un mensaje (mensajes efímeros estilo OpenClaw)."""
        if message_id is None:
            return None
        try:
            self._call("deleteMessage", {"chat_id": chat_id,
                                         "message_id": message_id})
        except Exception:
            pass
        return None


    def send_typing(self, chat_id):
        self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    def send_action(self, chat_id, action="typing"):
        try:
            self._call("sendChatAction", {"chat_id": chat_id, "action": action})
        except Exception:
            pass

    # ── voz / ficheros ──────────────────────────────────────────────────────
    def get_file_path(self, file_id: str):
        res = self._call("getFile", {"file_id": file_id})
        return res["result"]["file_path"]

    def download_file(self, file_path: str, dest: str):
        """Descarga un fichero de Telegram (p. ej. la nota de voz .oga) a `dest`."""
        url = FILE_API.format(token=self.token, path=urllib.parse.quote(file_path))
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = resp.read()
        with open(dest, "wb") as fh:
            fh.write(data)
        return dest

    def send_voice(self, chat_id, voice_path: str, caption: str = ""):
        """Envía una nota de voz (ogg/opus). Usa multipart (requests)."""
        import requests
        params = {"chat_id": chat_id}
        if caption:
            params["caption"] = caption
        with open(voice_path, "rb") as fh:
            res = requests.post(
                API.format(token=self.token, method="sendVoice"),
                data=params, files={"voice": fh}, timeout=180)
        res.raise_for_status()
        return res.json()

    def me_menciona(self, msg: dict, text: str) -> bool:
        """En un grupo respondemos SOLO si nos @mencionan o si es respuesta a
        un mensaje nuestro."""
        u = (self.username or "").lower()
        if u and ("@" + u) in (text or "").lower():
            return True
        rt = msg.get("reply_to_message") or {}
        frm = rt.get("from") or {}
        return bool(self.bot_id and frm.get("id") == self.bot_id)

    # ── bucle ──────────────────────────────────────────────────────────────
    def poll_once(self) -> int:
        params = {"timeout": 25, "offset": self._offset}
        try:
            res = self._call("getUpdates", params)
        except urllib.error.HTTPError as e:
            if e.code == 409 or e.code == 401:
                raise  # token inválido o conflicto de otro bot activo
            time.sleep(3)
            return 0
        except Exception:
            time.sleep(3)
            return 0

        n = 0
        for upd in res.get("result", []):
            self._offset = upd["update_id"] + 1
            n += 1
            msg = upd.get("message") or upd.get("channel_post")
            if not msg:
                continue
            chat_id = msg["chat"]["id"]
            if self.allowed and chat_id not in self.allowed:
                continue
            text = (msg.get("text") or "").strip()
            voice = msg.get("voice") or msg.get("video_note")
            if voice and voice.get("file_id") and self.on_voice:
                try:
                    self.on_voice(chat_id, voice["file_id"])
                except Exception as e:
                    import traceback; traceback.print_exc()
                    print(f'ERROR on_voz chat={chat_id}: {e}')
                continue
            if not text:
                continue
            # ── GRUPO: filtro por mencion + pizarra compartida ──
            entrante = text
            if str(chat_id).startswith("-"):
                quien = msg.get("from") or {}
                autor = ("@" + quien["username"]) if quien.get("username") else "humano"
                # 1) apuntamos lo que dice el humano en la pizarra (dedupe por msg_id)
                if grupo_bus is not None:
                    try:
                        grupo_bus.publicar(chat_id, autor, text,
                                           msg_id=msg.get("message_id"))
                    except Exception:  # noqa: BLE001
                        pass
                # 2) si NO nos mencionan, no respondemos (nada de contestar a todo)
                if not self.me_menciona(msg, text):
                    continue
                # 3) inyectamos como contexto lo ultimo que dijeron los demas
                if grupo_bus is not None:
                    try:
                        ctx = grupo_bus.formatear_contexto(chat_id, "@" + (self.username or ""))
                        if ctx:
                            entrante = ctx + "[Mensaje nuevo del humano]: " + text
                    except Exception:  # noqa: BLE001
                        pass
            if self.on_message:
                try:
                    self.on_message(chat_id, entrante)
                except Exception as e:
                    import traceback; traceback.print_exc()
                    print(f'ERROR on_message chat={chat_id}: {e}')
        return n

    def run_forever(self, sleep_s: float = 0.5):
        if self.disabled:
            # Sin Telegram: nos quedamos vivos para no tumbar el proceso (joker
            # es PID 1). El supervisor/web siguen funcionando normalmente.
            while True:
                time.sleep(3600)
        while True:
            try:
                self.poll_once()
            except urllib.error.HTTPError:
                time.sleep(3)
            time.sleep(sleep_s)
