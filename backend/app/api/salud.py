"""Chequeo de salud: estado del backend y de cada dependencia de datos."""

import logging

from fastapi import APIRouter, Response, status
from starlette.concurrency import run_in_threadpool

from app.datos import cache, mongo, sqlserver

router = APIRouter(prefix="/api", tags=["salud"])
log = logging.getLogger(__name__)


async def _sqlserver() -> None:
    await run_in_threadpool(sqlserver.verificar)


VERIFICACIONES = {
    "sqlserver": _sqlserver,
    "mongodb": mongo.verificar,
    "redis": cache.verificar,
}


@router.get("/salud")
async def salud(respuesta: Response) -> dict:
    """Devuelve 200 si todas las dependencias responden; 503 si alguna falla."""
    componentes: dict[str, str] = {}
    for nombre, verificar in VERIFICACIONES.items():
        try:
            await verificar()
            componentes[nombre] = "ok"
        except Exception as error:  # se informa cualquier falla sin cortar el chequeo
            log.warning("Dependencia %s no disponible: %s", nombre, error)
            componentes[nombre] = "error"

    sano = all(estado == "ok" for estado in componentes.values())
    if not sano:
        respuesta.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"estado": "ok" if sano else "degradado", "componentes": componentes}
