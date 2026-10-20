"""API REST del monitoreo. Todos los roles con el módulo leen; configuran Administrador y DBA."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.identidad.dependencias import requerir_modulo
from app.modulos.monitor import servicios
from app.modulos.monitor.entidades import Umbral

router = APIRouter(prefix="/api/monitor", tags=["monitor"])
contexto = Depends(requerir_modulo("monitor"))


class UmbralEntrada(BaseModel):
    metrica: str
    habilitada: bool = True
    warning: float | None = None
    critical: float | None = None


class UmbralesEntrada(BaseModel):
    umbrales: list[UmbralEntrada] = Field(max_length=50)


@router.get("")
async def listar(ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {
        "conexiones": await servicios.listar_conexiones(tenant),
        "puede_configurar": servicios.puede_configurar(sesion),
    }


@router.get("/{conexion_id}")
async def estado_actual(conexion_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await servicios.estado_actual(tenant, conexion_id)


@router.get("/{conexion_id}/serie")
async def serie(
    conexion_id: str, rango: str = "1h", metricas: str = Query("cpu"), ctx=contexto
) -> dict:
    tenant, _ = ctx
    return await servicios.serie(tenant, conexion_id, rango, metricas.split(","))


@router.get("/{conexion_id}/consultas-costosas")
async def consultas_costosas(conexion_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"consultas": await servicios.consultas_costosas(tenant, conexion_id)}


@router.get("/{conexion_id}/umbrales")
async def obtener_umbrales(conexion_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"umbrales": [u.__dict__ for u in await servicios.obtener_umbrales(tenant, conexion_id)]}


@router.put("/{conexion_id}/umbrales")
async def guardar_umbrales(conexion_id: str, datos: UmbralesEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    guardados = await servicios.guardar_umbrales(
        tenant, sesion, conexion_id, [Umbral(**u.model_dump()) for u in datos.umbrales]
    )
    return {"umbrales": [u.__dict__ for u in guardados]}
