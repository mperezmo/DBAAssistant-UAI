# Reverse proxy NGINX: termina TLS 1.3, entrega los estáticos y deriva el resto al backend.
# Se construye desde la raíz del repositorio (necesita las plantillas para compilar Tailwind).

FROM debian:bookworm-slim AS estilos
ARG TAILWIND_VERSION=4.3.3
ADD --chmod=755 https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/tailwindcss-linux-x64 /usr/local/bin/tailwindcss
WORKDIR /src/backend
COPY backend/tailwind/entrada.css backend/tailwind/diseno.css ./tailwind/
COPY backend/app/web ./app/web
RUN tailwindcss -i tailwind/entrada.css -o app/web/static/css/app.css --minify

FROM nginx:1.29
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx/certificado-desarrollo.sh /docker-entrypoint.d/40-certificado-desarrollo.sh
COPY --from=estilos /src/backend/app/web/static /usr/share/nginx/static
