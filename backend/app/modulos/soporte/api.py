"""API de Soporte (solo Administrador): bitácoras técnicas, historia de la base de la
aplicación, salud de los servicios y colas."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.identidad.dependencias import requerir_modulo
from app.modulos.auditoria import api as auditoria_api
from app.modulos.auditoria import servicios as auditoria
from app.modulos.auditoria.servicios import Filtros
from app.modulos.soporte import historia, servicios

router = APIRouter(prefix="/api/soporte", tags=["soporte"])
contexto = Depends(requerir_modulo("soporte"))
Nombre = Literal["sistema", "excepciones"]


class NivelEntrada(BaseModel):
    nivel: str


# --- Bitácoras --------------------------------------------------------------------


@router.get("/bitacoras")
async def resumen(ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.resumen_bitacoras(tenant, sesion)


@router.put("/bitacoras/excepciones/nivel")
async def cambiar_nivel(datos: NivelEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {"nivel": await auditoria.cambiar_nivel_excepciones(tenant, sesion, datos.nivel)}


@router.get("/bitacoras/{nombre}")
async def listar(
    nombre: Nombre,
    pagina: int = 1,
    f: Filtros = Depends(auditoria_api.filtros),
    ctx=contexto,
) -> dict:
    tenant, sesion = ctx
    return await auditoria.listar(tenant, sesion, nombre, f, pagina)


@router.get("/bitacoras/{nombre}/registros/{registro_id}")
async def detalle_registro(nombre: Nombre, registro_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await auditoria.detalle(tenant, sesion, nombre, registro_id)


@router.get("/bitacoras/{nombre}/exportar")
async def exportar(
    nombre: Nombre,
    formato: Literal["csv", "pdf"],
    f: Filtros = Depends(auditoria_api.filtros),
    ctx=contexto,
):
    tenant, sesion = ctx
    return await auditoria_api.respuesta_exportacion(tenant, sesion, nombre, formato, f)


# --- Servicios y colas ------------------------------------------------------------


@router.get("/servicios")
async def salud(ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"servicios": await servicios.servicios(tenant)}


@router.get("/procesos")
async def procesos(ctx=contexto) -> dict:
    return servicios.colas_y_procesos()


# --- Historia de la base de la aplicación -----------------------------------------


@router.get("/estructura")
async def versiones(ctx=contexto) -> dict:
    tenant, _ = ctx
    return await historia.versiones(tenant)


@router.post("/estructura")
async def capturar(ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await historia.capturar_ahora(tenant, sesion)


@router.get("/estructura/comparar")
async def comparar(desde: int, hasta: str = historia.ACTUAL, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await historia.comparar(tenant, desde, hasta)


@router.get("/estructura/{version}")
async def version(version: int, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await historia.detalle(tenant, version)


@router.get("/estructura/{version}/script")
async def script(version: int, ctx=contexto) -> PlainTextResponse:
    tenant, _ = ctx
    archivo = f"estructura_{tenant.base_sqlserver}_v{version}.sql"
    return PlainTextResponse(
        await historia.script(tenant, version),
        headers={"Content-Disposition": f'attachment; filename="{archivo}"'},
    )


@router.get("/ddl")
async def historial_ddl(
    pagina: int = 1, texto: str | None = Query(default=None, max_length=200), ctx=contexto
) -> dict:
    tenant, _ = ctx
    return await historia.historial_ddl(tenant, pagina, texto)


@router.get("/ddl/{ddl_id}")
async def ddl(ddl_id: int, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await historia.ddl(tenant, ddl_id)


@router.get("/datos")
async def tablas(ctx=contexto) -> dict:
    return {"tablas": historia.tablas()}


@router.get("/datos/{tabla}")
async def datos(
    tabla: str,
    modo: str = "momento",
    momento: datetime | None = None,
    clave: str | None = Query(default=None, max_length=400),
    ctx=contexto,
) -> dict:
    tenant, _ = ctx
    return await historia.datos(tenant, tabla, modo, momento, clave)
