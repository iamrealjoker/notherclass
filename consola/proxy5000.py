# -*- coding: utf-8 -*-
"""proxy5000.py — UN SOLO PUERTO (5000) que habla HTTP **y** HTTPS.

¿POR QUÉ?
En Docker el 5000 es el ÚNICO puerto publicado. El micrófono del navegador
(getUserMedia) exige "contexto seguro" (HTTPS o localhost): por http://<ip>:5000
desde el móvil el navegador BLOQUEA la voz. Pero si pusiéramos HTTPS en el 5000,
se romperían los curl internos (http://127.0.0.1:5000).

SOLUCIÓN: este conmutador transparente. Mira el PRIMER byte de cada conexión:
  · 0x16  -> es un ClientHello TLS  -> la atiende con TLS (certificado autofirmado)
  · otro  -> es HTTP normal         -> la pasa tal cual
En ambos casos reenvía al MISMO backend Flask interno (127.0.0.1:5051), que NO
está publicado. Así:
  · http://<ip>:5000   sigue funcionando igual (curl, health-checks, agentes)
  · https://<ip>:5000  funciona y habilita el micrófono (aceptas el aviso 1 vez)

Genera el certificado con:  bash consola/hacer_cert.sh <TU_IP>
"""
import os
import socket
import ssl
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ESCUCHA = ("0.0.0.0", int(os.environ.get("TW_PROXY_PORT", "5000")))
BACKEND = ("127.0.0.1", int(os.environ.get("TW_CONSOLA_PUERTO", "5051")))
CERT = os.environ.get("TW_TLS_CERT", os.path.join(HERE, "certs", "cert.pem"))
CLAVE = os.environ.get("TW_TLS_KEY", os.path.join(HERE, "certs", "clave.pem"))
_TLS_OK = os.path.exists(CERT) and os.path.exists(CLAVE)
_CONTEXTO = None
if _TLS_OK:
    _CONTEXTO = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    _CONTEXTO.load_cert_chain(CERT, CLAVE)


def _bombear(origen, destino):
    """Copia bytes en un sentido hasta que se cierre."""
    try:
        while True:
            datos = origen.recv(65536)
            if not datos:
                break
            destino.sendall(datos)
    except OSError:
        pass
    finally:
        try:
            destino.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _atender(cli, dir_cli):
    tls = False
    try:
        cli.settimeout(15)
        # MSG_PEEK: mirar sin consumir -> si es TLS lo desciframos nosotros.
        primero = cli.recv(1, socket.MSG_PEEK) if hasattr(socket, "MSG_PEEK") else b""
        if primero[:1] == b"\x16":
            if _CONTEXTO is None:
                print("[proxy5000] ⚠️ llega un cliente HTTPS pero NO hay certificado. "
                      "Genera: bash consola/hacer_cert.sh <TU_IP>", flush=True)
                cli.close()
                return
            cli = _CONTEXTO.wrap_socket(cli, server_side=True)
            tls = True
        cli.settimeout(None)
        arriba = socket.create_connection(BACKEND, timeout=15)
        arriba.settimeout(None)
    except Exception as e:  # noqa: BLE001  (handshake fallido, backend caído…)
        try:
            print(f"[proxy5000] ⚠️ {dir_cli}: {e}", flush=True)
        except Exception:  # noqa: BLE001
            pass
        for s in (cli,):
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        return
    try:
        h = threading.Thread(target=_bombear, args=(cli, arriba), daemon=True)
        h.start()
        _bombear(arriba, cli)
        h.join(timeout=5)
    finally:
        for s in (cli, arriba):
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(ESCUCHA)
    srv.listen(128)
    print(f"[proxy5000] escuchando en {ESCUCHA[0]}:{ESCUCHA[1]} → backend "
          f"{BACKEND[0]}:{BACKEND[1]} · HTTPS={'sí' if _TLS_OK else 'NO (sin cert)'}",
          flush=True)
    while True:
        try:
            cli, dir_cli = srv.accept()
        except OSError:
            continue
        threading.Thread(target=_atender, args=(cli, dir_cli), daemon=True).start()


if __name__ == "__main__":
    main()
