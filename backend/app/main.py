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
from app.core import contexto, excepciones
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.core.registro import configurar_registro
from app.identidad import api as identidad
from app.identidad import api_usuarios
from app.modulos.alertas import api as alertas
from app.modulos.alertas.motor import Motor
from app.modulos.auditoria import api as auditoria
from app.modulos.chat_ia import api as chat_ia
from app.modulos.conexiones import api as conexiones
from app.modulos.contexto_negocio import api as contexto_negocio
from app.modulos.historico_schema import api as historico_schema
from app.modulos.inicio import api as inicio
from app.modulos.monitor import api as monitor
from app.modulos.monitor.recolector import Recolector
from app.modulos.sandbox import api as sandbox
from app.modulos.sandbox.programador import Programador
from app.modulos.web_push import api as web_push
from app.modulos.workarounds import api as workarounds
from app.modulos.workarounds import servicios as workarounds_servicios
from app.modulos.workarounds.servicios import Automatizador
from app.tenants import repositorios as tenants_repositorios
from app.tenants import resolucion
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
    excepciones.iniciar(
        lambda: {
            t.slug: t.base_mongodb for t in tenants_repositorios.listar() if t.estado == "activo"
        }
    )
    await workarounds_servicios.cerrar_interrumpidas_todo(
        await run_in_threadpool(tenants.tenants_activos)
    )

    recolector, tarea = None, None
    if obtener_configuracion().monitor_recolector_activo:
        recolector = Recolector(evaluador=Motor(automatizador=Automatizador()))
        tarea = asyncio.create_task(recolector.ejecutar())
    programador = None
    if obtener_configuracion().sandbox_programador_activo:
        programador = asyncio.create_task(Programador().ejecutar())
    yield
    excepciones.detener()
    if programador:
        programador.cancel()
    if tarea:
        tarea.cancel()
        recolector.detener()


async def manejar_error_dominio(request: Request, error: ErrorDominio):
    """Errores de negocio: JSON para la API y página de error para el navegador."""
    log.info("Error de dominio %s en %s: %s", error.codigo, request.url.path, error.detalle)
    estado = error.definicion.estado_http
    if request.url.path.startswith("/api/"):
        cuerpo = {"codigo": error.codigo, "mensaje": error.definicion.mensaje}
        if error.datos:
            cuerpo["datos"] = error.datos
        return JSONResponse(cuerpo, status_code=estado)
    contexto = {
        "codigo": error.codigo,
        "mensaje": error.definicion.mensaje,
        "hay_tenant": error.codigo not in ("TEN_VAL_000",),
    }
    return plantillas.TemplateResponse(request, "paginas/error.html", contexto, status_code=estado)


async def registrar_contexto(request: Request, siguiente):
    """Deja a mano el tenant (por el subdominio), la IP y la ruta de la petición."""
    contexto.ip.set(request.client.host if request.client else None)
    contexto.ruta.set(request.url.path)
    contexto.tenant_slug.set(
        resolucion.slug_desde_host(
            request.headers.get("host", ""), obtener_configuracion().dominio_base
        )
    )
    return await siguiente(request)


def crear_app() -> FastAPI:
    cfg = obtener_configuracion()
    configurar_registro(cfg.log_nivel)

    app = FastAPI(title=cfg.app_nombre, version="0.1.0", lifespan=ciclo_de_vida)
    app.add_exception_handler(ErrorDominio, manejar_error_dominio)
    app.middleware("http")(registrar_contexto)
    app.include_router(salud.router)
    app.include_router(identidad.router)
    app.include_router(api_usuarios.router)
    app.include_router(conexiones.router)
    app.include_router(monitor.router)
    app.include_router(alertas.router)
    app.include_router(web_push.router)
    app.include_router(workarounds.router)
    app.include_router(sandbox.router)
    app.include_router(chat_ia.router)
    app.include_router(contexto_negocio.router)
    app.include_router(inicio.router)
    app.include_router(auditoria.router)
    app.include_router(historico_schema.router)
    app.include_router(web.router)
    # En Docker los estáticos los entrega NGINX; este montaje cubre el desarrollo sin proxy.
    app.mount("/static", StaticFiles(directory=DIRECTORIO_ESTATICOS), name="static")
    return app


app = crear_app()
