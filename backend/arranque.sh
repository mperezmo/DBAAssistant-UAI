#!/bin/sh
# Arranque del backend: Uvicorn sirve HTTPS (TLS) hacia NGINX con el certificado interno.
set -eu

python -m app.cli certificado-interno
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --ssl-certfile "${TLS_INTERNO_CERTIFICADO:-/srv/tls/backend.crt}" \
  --ssl-keyfile "${TLS_INTERNO_CLAVE:-/srv/tls/backend.key}" \
  --proxy-headers --forwarded-allow-ips "*"
