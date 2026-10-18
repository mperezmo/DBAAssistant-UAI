"""Configuración del registro de eventos (logging) de la aplicación."""

import logging
import sys

FORMATO = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"


def configurar_registro(nivel: str) -> None:
    """Envía los logs a la salida estándar, visible con ``docker compose logs``."""
    logging.basicConfig(level=nivel.upper(), format=FORMATO, stream=sys.stdout, force=True)
