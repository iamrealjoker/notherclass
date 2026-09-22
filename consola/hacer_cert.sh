#!/bin/bash
# hacer_ca.sh — CA LOCAL + certificado del servidor para que el MÓVIL CONFIÍE.
#
# ¿Por qué? Un certificado autofirmado "suelto" hace que el navegador marque el
# origen como NO válido y (Chrome/Safari) BLOQUEE el micrófono. Con una CA local
# instalada en el móvil, https://<TU_IP>:5000 es de CONFIANZA y el micro funciona
# sin avisos. No necesita internet ni túneles.
#
# Uso:  bash hacer_ca.sh 192.168.1.50            # ← pon TU IP (la que usas en el móvil)
#       bash hacer_ca.sh 192.168.1.50 10.0.0.7   # puedes pasar varias
#
# Después: INSTALA consola/certs/ca.crt en el móvil (instrucciones al final).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$DIR"
IPS="${*:-127.0.0.1}"
SAN="DNS:localhost,IP:127.0.0.1"
for ip in $IPS; do SAN="$SAN,IP:$ip"; done

# 1) CA local (si ya existe NO se regenera: así no invalidamos la que instalaste)
if [ ! -f "$DIR/ca.crt" ] || [ ! -f "$DIR/ca.key" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$DIR/ca.key" -out "$DIR/ca.crt" \
    -subj "/CN=NotherClass CA (local)" 2>/dev/null
  echo "🆕 CA creada: $DIR/ca.crt"
else
  echo "♻️  Uso la CA existente: $DIR/ca.crt (no se regenera)"
fi

# 2) Certificado del SERVIDOR firmado por la CA, con las IPs en el SAN
openssl req -newkey rsa:2048 -nodes -keyout "$DIR/clave.pem" \
  -out "$DIR/solicitud.csr" -subj "/CN=notherclass-consola" \
  -addext "subjectAltName=$SAN" 2>/dev/null
openssl x509 -req -in "$DIR/solicitud.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" \
  -CAcreateserial -out "$DIR/cert.pem" -days 3650 \
  -extfile <(printf "subjectAltName=%s" "$SAN") 2>/dev/null
rm -f "$DIR/solicitud.csr"

echo "✅ Certificado del servidor: $DIR/cert.pem"
echo "   SAN: $SAN"
echo "   (la consola lo cogerá al reiniciar:  python3 reiniciar_agentes.py --solo proxy5000)"
echo
echo "📲 INSTALA LA CA EN EL MÓVIL (una sola vez):  $DIR/ca.crt"
echo "   · Android: Ajustes → Seguridad → Cifrado y credenciales → Instalar un"
echo "     certificado → 'Certificado de CA' → elige ca.crt. Aviso 'Red puede"
echo "     ser supervisada' = OK (es tu propia CA)."
echo "   · iPhone:  pasa ca.crt al móvil (AirDrop/archivo) → Ajustes → Perfil"
echo "     descargado → Instalar. LUEGO: Ajustes → General → Información →"
echo "     Ajustes de confianza de certificados → ACTIVA 'NotherClass CA (local)'."
echo "   · Luego abre:  https://<TU_IP>:5000   (verde, sin avisos, micro OK)"

