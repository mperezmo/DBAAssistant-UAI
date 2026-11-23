"""Latidos de los procesos en segundo plano del backend, para el panel de Soporte.

Cada proceso (recolector de métricas, programador del Sandbox) informa al terminar cada
vuelta si la hizo (``activo``: tiene el candado de Redis) o si quedó en espera porque otra
réplica lo tiene. El registro vive en la memoria del proceso: muestra lo que hace esta
réplica del backend.
"""

from datetime import UTC, datetime

_latidos: dict[str, dict] = {}


def latido(nombre: str, intervalo_segundos: float, activo: bool, **datos) -> None:
    _latidos[nombre] = {
        "ts": datetime.now(UTC),
        "intervalo_segundos": intervalo_segundos,
        "activo": activo,
        **datos,
    }


def latidos() -> dict[str, dict]:
    return {nombre: dict(datos) for nombre, datos in _latidos.items()}


def limpiar() -> None:
    _latidos.clear()
