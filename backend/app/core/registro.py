"""Configuración del registro de eventos (logging) de la aplicación."""

import logging
import sys

FORMATO = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"


def configurar_registro(nivel: str) -> None:
    """Envía los logs a la salida estándar, visible con ``docker compose logs``."""
    # Se usa sys.__stdout__ (y no sys.stdout) para que el handler no quede atado a un
    # flujo reemplazado temporalmente, como ocurre en las pruebas.
    logging.basicConfig(level=nivel.upper(), format=FORMATO, stream=sys.__stdout__, force=True)
    # El nivel queda también en el handler: la bitácora de excepciones puede pedir que
    # los loggers de la aplicación generen registros más detallados que los de la consola.
    for manejador in logging.getLogger().handlers:
        manejador.setLevel(nivel.upper())
