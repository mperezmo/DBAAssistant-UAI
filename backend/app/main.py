"""Punto de entrada de la aplicación FastAPI (servida por Uvicorn)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.api import salud
from app.core.config import obtener_configuracion
from app.core.registro import configurar_registro
from app.tenants import servicios as tenants
from app.web import rutas as web
from app.web.rutas import DIRECTORIO_ESTATICOS


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
    # Al arrancar se aplican los cambios de estructura pendientes en el catálogo
    # y en las bases de todos los tenants.
    await run_in_threadpool(tenants.migrar_todo)
    yield


def crear_app() -> FastAPI:
    cfg = obtener_configuracion()
    configurar_registro(cfg.log_nivel)

    app = FastAPI(title=cfg.app_nombre, version="0.1.0", lifespan=ciclo_de_vida)
    app.include_router(salud.router)
    app.include_router(web.router)
    # En Docker los estáticos los entrega NGINX; este montaje cubre el desarrollo sin proxy.
    app.mount("/static", StaticFiles(directory=DIRECTORIO_ESTATICOS), name="static")
    return app


app = crear_app()
