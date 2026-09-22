"""
fw_stt.py — transcripción con faster-whisper (se ejecuta con el venv Python 3.11).

Uso:  <venv>/bin/python fw_stt.py <audio.wav>          (CLI, una nota por llamada)
      <venv>/bin/python fw_stt.py --servir [puerto]    (RESIDENTE: carga el modelo
                                                        UNA vez y sirve por HTTP local)
Env:  FW_SIZE=base|tiny   FW_LANG=es|'' (vacío=auto)   FW_THREADS=8
      FW_SERVE_PORT=5077 (puerto del modo residente)
Salida: el texto transcrito por stdout (CLI) o como cuerpo HTTP (residente).

Modo residente: precarga el modelo una vez (lo que ahorra ~4-5s por nota).
Escucha en 127.0.0.1:POST.  POST /transcribir con el wav en el cuerpo ->
devuelve el texto.  GET /health -> {"ok": true}.
"""
import os
import sys
import time
import threading
import tempfile

from faster_whisper import WhisperModel

# Lock global: serializa las transcripciones (1 sola CPU) pero deja el hilo
# libre para atender /health y no colgar al resto de peticiones a la espera.
_LOCK = threading.Lock()


def _modelo():
    size = os.environ.get("FW_SIZE", "base")
    threads = int(os.environ.get("FW_THREADS", "8"))
    lang = os.environ.get("FW_LANG", "").strip() or None
    return WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=threads), lang


def main():
    if "--servir" in sys.argv:
        _servir()
        return
    wav = sys.argv[1]
    model, lang = _modelo()
    # language=None -> auto-detección de idioma por faster-whisper.
    # (antes el default era "es" y forzaba español: rompía el inglés del traductor).
    segments, _info = model.transcribe(wav, language=lang, beam_size=1)
    text = "".join(seg.text for seg in segments).strip()
    print(text)


def _servir():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    port = int(os.environ.get("FW_SERVE_PORT",
                              sys.argv[sys.argv.index("--servir") + 1]
                              if len(sys.argv) > sys.argv.index("--servir") + 1
                              else "5077"))
    t0 = time.time()
    model, lang = _modelo()
    print(f"[fw_stt] residente listo: FW_SIZE={os.environ.get('FW_SIZE', 'base')} "
          f"FW_LANG={lang or 'auto'} en {time.time() - t0:.1f}s, puerto {port}", flush=True)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _idioma_peticion(self):
            """Idioma POR PETICIÓN: ?lang=es|en|auto (o cabecera X-FW-Lang).

            Sin parámetro se usa el idioma del residente (FW_LANG, o auto).
            Así el MISMO servidor sirve al agente (español fijo: evita que una
            nota en español se transcriba como coreano) y al traductor (auto),
            sin reiniciar ni recargar el modelo.
            """
            from urllib.parse import urlparse, parse_qs
            q = (parse_qs(urlparse(self.path).query).get("lang", [""])[0]
                 or self.headers.get("X-FW-Lang", "")).strip().lower()
            if not q:
                return lang
            if q in ("auto", "none"):
                return None
            return q[:2]          # es, en, ko... (código ISO-639-1)

        def do_POST(self):
            wav = os.path.join(tempfile.gettempdir(),
                               f"fw_stt_{os.getpid()}_{int(time.time() * 1000)}.wav")
            try:
                n = int(self.headers.get("Content-Length", 0))
                with open(wav, "wb") as f:
                    f.write(self.rfile.read(n))
                with _LOCK:
                    segments, _info = model.transcribe(wav, language=self._idioma_peticion(),
                                                       beam_size=1)
                text = "".join(seg.text for seg in segments).strip()
                body, code = text.encode("utf-8"), 200
            except Exception as e:
                body, code = f"error: {e}".encode("utf-8"), 500
            finally:
                try:
                    os.remove(wav)
                except OSError:
                    pass
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
