#!/bin/sh
# NGINX necesita el certificado del backend para verificar el tramo interno por HTTPS.
# Lo genera el backend al arrancar: se espera hasta un minuto a que aparezca.
set -eu

CERTIFICADO=/etc/nginx/interno/backend.crt
for _ in $(seq 1 60); do
  [ -s "$CERTIFICADO" ] && exit 0
  sleep 1
done
echo "No apareció $CERTIFICADO: ¿arrancó el backend?" >&2
exit 1
