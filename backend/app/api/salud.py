"""Chequeo de salud: estado del backend y de cada dependencia de datos."""

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Response, status
from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.datos import cache, mongo, sqlserver
from app.identidad.auth0 import ClienteAuth0
from app.identidad.dependencias import requerir_modulo
from app.web.i18n import traducir

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


async def _medir(verificar: Callable[[], Awaitable[None]]) -> tuple[str, int | None]:
    inicio = time.perf_counter()
    try:
        await verificar()
    except Exception as error:  # el panel muestra el servicio caído sin cortar el resto
        log.warning("Servicio no disponible: %s", error)
        return "error", None
    return "online", round((time.perf_counter() - inicio) * 1000)


contexto_admin = Depends(requerir_modulo("admin"))


@router.get("/salud/servicios")
async def servicios(ctx=contexto_admin) -> dict:
    """Estado de los servicios para las tarjetas del Panel Admin."""
    tenant, _ = ctx
    cfg = obtener_configuracion()
    tarjetas = [
        ("SQL Server", VERIFICACIONES["sqlserver"], tenant.base_sqlserver),
        ("MongoDB", VERIFICACIONES["mongodb"], tenant.base_mongodb),
        ("Redis", VERIFICACIONES["redis"], traducir("servicios.redis.detalle")),
    ]
    resultado = []
    for nombre, verificar, detalle in tarjetas:
        estado, latencia = await _medir(verificar)
        resultado.append(
            {"nombre": nombre, "estado": estado, "latencia_ms": latencia, "detalle": detalle}
        )

    if cfg.auth0_configurado:
        estado, latencia = await _medir(ClienteAuth0(cfg).verificar)
        detalle = cfg.auth0_domain
    else:
        estado, latencia, detalle = "warning", None, traducir("servicios.no_configurado")
    resultado.append(
        {"nombre": "Auth0", "estado": estado, "latencia_ms": latencia, "detalle": detalle}
    )
    return {"servicios": resultado}
