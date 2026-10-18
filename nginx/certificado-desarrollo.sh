#!/bin/sh
# Genera un certificado autofirmado si no se montó uno real en /etc/nginx/certs.
# Solo para desarrollo local: el navegador mostrará una advertencia de seguridad.
set -eu

DIR=/etc/nginx/certs
if [ ! -f "$DIR/servidor.crt" ] || [ ! -f "$DIR/servidor.key" ]; then
  mkdir -p "$DIR"
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1" \
    -keyout "$DIR/servidor.key" -out "$DIR/servidor.crt" 2>/dev/null
  echo "Certificado autofirmado generado en $DIR"
fi
