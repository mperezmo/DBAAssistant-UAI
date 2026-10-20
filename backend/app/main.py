"""Punto de entrada de la aplicación FastAPI (servida por Uvicorn)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pyodbc
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.api import salud
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.core.registro import configurar_registro
from app.identidad import api as identidad
from app.identidad import api_usuarios
from app.modulos.conexiones import api as conexiones
from app.modulos.monitor import api as monitor
from app.modulos.monitor.recolector import Recolector
from app.tenants import servicios as tenants
from app.web import rutas as web
from app.web.rutas import DIRECTORIO_ESTATICOS, plantillas

log = logging.getLogger(__name__)
INTENTOS_INICIO = 10
ESPERA_ENTRE_INTENTOS_SEGUNDOS = 5


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
    # Al arrancar se aplican los cambios de estructura pendientes en el catálogo
    # y en las bases de todos los tenants.
    # Se reintenta porque, al levantar todo junto, SQL Server puede tardar en aceptar
    # conexiones aunque el contenedor ya figure iniciado.
    for intento in range(1, INTENTOS_INICIO + 1):
        try:
            await run_in_threadpool(tenants.migrar_todo)
            break
        except pyodbc.OperationalError as error:
            if intento == INTENTOS_INICIO:
                raise
            log.warning("SQL Server no disponible (intento %s): %s", intento, error)
            await asyncio.sleep(ESPERA_ENTRE_INTENTOS_SEGUNDOS)
    await tenants.preparar_mongo_todo()

    recolector, tarea = None, None
    if obtener_configuracion().monitor_recolector_activo:
        recolector = Recolector()
        tarea = asyncio.create_task(recolector.ejecutar())
    yield
    if tarea:
        tarea.cancel()
        recolector.detener()


async def manejar_error_dominio(request: Request, error: ErrorDominio):
    """Errores de negocio: JSON para la API y página de error para el navegador."""
    log.info("Error de dominio %s en %s: %s", error.codigo, request.url.path, error.detalle)
    estado = error.definicion.estado_http
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"codigo": error.codigo, "mensaje": error.definicion.mensaje}, status_code=estado
        )
    contexto = {
        "codigo": error.codigo,
        "mensaje": error.definicion.mensaje,
        "hay_tenant": error.codigo not in ("TEN_VAL_000",),
    }
    return plantillas.TemplateResponse(request, "paginas/error.html", contexto, status_code=estado)


def crear_app() -> FastAPI:
    cfg = obtener_configuracion()
    configurar_registro(cfg.log_nivel)

    app = FastAPI(title=cfg.app_nombre, version="0.1.0", lifespan=ciclo_de_vida)
    app.add_exception_handler(ErrorDominio, manejar_error_dominio)
    app.include_router(salud.router)
    app.include_router(identidad.router)
    app.include_router(api_usuarios.router)
    app.include_router(conexiones.router)
    app.include_router(monitor.router)
    app.include_router(web.router)
    # En Docker los estáticos los entrega NGINX; este montaje cubre el desarrollo sin proxy.
    app.mount("/static", StaticFiles(directory=DIRECTORIO_ESTATICOS), name="static")
    return app


app = crear_app()
