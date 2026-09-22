#!/bin/sh
# tunel.sh — placeholder
#
# ⚠️ AVISO DE SEGURIDAD:
# Este script abría un túnel rápido (cloudflared / trycloudflare) apuntando a la
# consola del puerto 5000. Un túnel rápido NO atraviesa tu proxy inverso, así que
# **se salta la autenticación**: cualquiera con la URL entra a la consola, que
# puede EJECUTAR COMANDOS en la máquina.
#
# Correcto para exponer la consola:
#   - Proxy inverso (nginx/Caddy) con HTTPS + autenticación (Basic Auth/OAuth/mTLS)
#   - O una VPN (WireGuard, Tailscale…) / túnel con autenticación
#
# Si solo quieres un túnel TEMPORAL para probar, protégelo tú mismo y ciérralo al
# terminar. Nunca lo dejes abierto. Este fichero no hace nada.
echo "tunel.sh está desactivado por seguridad (ver comentarios del fichero)."
exit 1
