# -*- coding: utf-8 -*-
"""consola_interna.py — la consola Flask en un puerto INTERNO (5051).

La publica en el 5000 el multiplexor `proxy5000.py` (HTTP + HTTPS en el mismo
puerto). Este proceso es el que NO debe ser accesible desde fuera.
"""
import os

os.environ.setdefault("TW_CONSOLA_PUERTO", "5051")
os.environ["TW_CONSOLA_PUERTO"] = os.environ.get("TW_INTERNO", "5051")

from app import app  # noqa: E402  (importar NO arranca nada: está bajo __main__)

PUERTO = int(os.environ["TW_CONSOLA_PUERTO"])

if __name__ == "__main__":
    print(f"[consola-interna] Flask en 127.0.0.1:{PUERTO} (detrás de proxy5000)",
          flush=True)
    app.run(host="0.0.0.0", port=PUERTO, debug=False, use_reloader=False,
            threaded=True)
