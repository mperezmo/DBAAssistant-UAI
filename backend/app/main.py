"""Punto de entrada de la aplicación FastAPI (servida por Uvicorn)."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import salud
from app.core.config import obtener_configuracion
from app.core.registro import configurar_registro
from app.web import rutas as web
from app.web.rutas import DIRECTORIO_ESTATICOS


def crear_app() -> FastAPI:
    cfg = obtener_configuracion()
    configurar_registro(cfg.log_nivel)

    app = FastAPI(title=cfg.app_nombre, version="0.1.0")
    app.include_router(salud.router)
    app.include_router(web.router)
    # En Docker los estáticos los entrega NGINX; este montaje cubre el desarrollo sin proxy.
    app.mount("/static", StaticFiles(directory=DIRECTORIO_ESTATICOS), name="static")
    return app


app = crear_app()
