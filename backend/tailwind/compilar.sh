#!/usr/bin/env sh
# Compila los estilos para desarrollo local (fuera de Docker).
# Descarga el ejecutable de Tailwind la primera vez y lo deja en backend/tailwind/bin.
# Uso: ./backend/tailwind/compilar.sh [--watch]
set -eu

VERSION="4.3.3"
DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$DIR/bin/tailwindcss-$VERSION"

if [ ! -x "$BIN" ]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)  PLATAFORMA="linux-x64" ;;
    Linux-aarch64) PLATAFORMA="linux-arm64" ;;
    Darwin-arm64)  PLATAFORMA="macos-arm64" ;;
    Darwin-x86_64) PLATAFORMA="macos-x64" ;;
    *) echo "Plataforma no soportada: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
  esac
  mkdir -p "$DIR/bin"
  curl -sSL -o "$BIN" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/v$VERSION/tailwindcss-$PLATAFORMA"
  chmod +x "$BIN"
fi

"$BIN" -i "$DIR/entrada.css" -o "$DIR/../app/web/static/css/app.css" --minify "$@"
