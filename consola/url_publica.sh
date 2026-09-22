#!/bin/sh
# url_publica.sh — placeholder
#
# ⚠️ DATO IMPORTANTE DE SEGURIDAD:
# En versiones anteriores este script abría un TÚNEL PÚBLICO (cloudflared
# trycloudflare) hacia la consola del puerto 5000. Ese túnel NO pasa por un
# proxy inverso, así que SE SALTABA cualquier autenticación y exponía la consola
# (que puede EJECUTAR COMANDOS) a Internet sin protección.
#
# NO uses túneles rápidos para publicar la consola. Si necesitas acceso externo:
#   1) Pon un proxy inverso (nginx/Caddy) DELANTE con autenticación (Basic Auth,
#      OAuth, mTLS...) y HTTPS.
#   2) O usa una VPN / túnel con autenticación.
#
# Este fichero se deja como recordatorio; no hace nada.
echo "url_publica.sh está desactivado por seguridad (ver comentarios del fichero)."
echo "Para publicar la consola, usa un proxy inverso con autenticación (nginx/Caddy) + HTTPS."
exit 1
