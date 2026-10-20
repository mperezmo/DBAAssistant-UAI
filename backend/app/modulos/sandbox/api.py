"""API REST del Sandbox (Administrador y DBA; promueve solo el DBA)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.identidad.dependencias import requerir_modulo
from app.modulos.sandbox import servicios

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])
contexto = Depends(requerir_modulo("sandbox"))


class EjecucionEntrada(BaseModel):
    conexion_id: str
    base: str = Field(max_length=128)
    sql: str = Field(max_length=servicios.LARGO_MAXIMO_SQL)
    solo_sintaxis: bool = False


@router.get("/conexiones/{conexion_id}")
async def estado(conexion_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await servicios.estado(tenant, conexion_id)


@router.post("/conexiones/{conexion_id}/sincronizar", status_code=202)
async def sincronizar(conexion_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    await servicios.sincronizar(tenant, sesion, conexion_id)
    return await servicios.estado(tenant, conexion_id)


@router.post("/ejecutar")
async def ejecutar(datos: EjecucionEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.ejecutar(
        tenant, sesion, datos.conexion_id, datos.base, datos.sql, datos.solo_sintaxis
    )


@router.get("/historial")
async def historial(
    conexion_id: str | None = None,
    usuario: str | None = None,
    estado: str | None = None,
    ctx=contexto,
) -> dict:
    tenant, sesion = ctx
    return {
        "registros": await servicios.historial(tenant, conexion_id, usuario, estado),
        "puede_promover": sesion.rol in servicios.ROLES_PROMUEVEN,
    }


@router.get("/historial/{registro_id}")
async def detalle(registro_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await servicios.detalle(tenant, registro_id)


@router.post("/historial/{registro_id}/previa")
async def previa(registro_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.previa(tenant, sesion, registro_id)


@router.post("/historial/{registro_id}/promover")
async def promover(registro_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.promover(tenant, sesion, registro_id)
