"""Punto de entrada de la aplicación FastAPI (servida por Uvicorn)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
from app.tenants import servicios as tenants
from app.web import rutas as web
from app.web.rutas import DIRECTORIO_ESTATICOS, plantillas

log = logging.getLogger(__name__)


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
    # Al arrancar se aplican los cambios de estructura pendientes en el catálogo
    # y en las bases de todos los tenants.
    await run_in_threadpool(tenants.migrar_todo)
    yield


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
    app.include_router(web.router)
    # En Docker los estáticos los entrega NGINX; este montaje cubre el desarrollo sin proxy.
    app.mount("/static", StaticFiles(directory=DIRECTORIO_ESTATICOS), name="static")
    return app


app = crear_app()
