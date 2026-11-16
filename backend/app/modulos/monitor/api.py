"""API REST del monitoreo. Todos los roles con el módulo leen; configuran Administrador y DBA."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.identidad.dependencias import requerir_modulo
from app.modulos.monitor import servicios
from app.modulos.monitor.entidades import (
    FULL_HORAS_POR_DEFECTO,
    LOG_MINUTOS_POR_DEFECTO,
    PoliticaBackup,
    Umbral,
)

router = APIRouter(prefix="/api/monitor", tags=["monitor"])
contexto = Depends(requerir_modulo("monitor"))


class UmbralEntrada(BaseModel):
    metrica: str
    habilitada: bool = True
    warning: float | None = None
    critical: float | None = None


class UmbralesEntrada(BaseModel):
    umbrales: list[UmbralEntrada] = Field(max_length=50)


class PoliticaEntrada(BaseModel):
    habilitada: bool = True
    full_horas: int = FULL_HORAS_POR_DEFECTO
    log_minutos: int = LOG_MINUTOS_POR_DEFECTO


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


@router.get("/{conexion_id}/backups/politica")
async def obtener_politica(conexion_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return asdict(await servicios.obtener_politica(tenant, conexion_id))


@router.put("/{conexion_id}/backups/politica")
async def guardar_politica(conexion_id: str, datos: PoliticaEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    politica = PoliticaBackup(**datos.model_dump())
    return asdict(await servicios.guardar_politica(tenant, sesion, conexion_id, politica))


@router.get("/{conexion_id}/backups/historial")
async def historial_backups(conexion_id: str, base: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"backups": await servicios.historial_backups(tenant, conexion_id, base)}


@router.get("/{conexion_id}/jobs/{job_id}")
async def historial_job(conexion_id: str, job_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"ejecuciones": await servicios.historial_job(tenant, conexion_id, job_id)}


@router.post("/{conexion_id}/jobs/{job_id}/explicar")
async def explicar_job(conexion_id: str, job_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.explicar_job(tenant, sesion, conexion_id, job_id)


@router.get("/{conexion_id}/query-store")
async def estado_query_store(conexion_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await servicios.estado_query_store(tenant, conexion_id)


@router.get("/{conexion_id}/query-store/consultas")
async def consultas_query_store(
    conexion_id: str, base: str, metrica: str = "cpu", periodo: str = "24h", ctx=contexto
) -> dict:
    tenant, _ = ctx
    return await servicios.consultas_query_store(tenant, conexion_id, base, metrica, periodo)


@router.get("/{conexion_id}/query-store/consulta")
async def consulta_query_store(
    conexion_id: str,
    base: str,
    query_id: int,
    metrica: str = "cpu",
    periodo: str = "24h",
    ctx=contexto,
) -> dict:
    tenant, _ = ctx
    return await servicios.consulta_query_store(
        tenant, conexion_id, base, query_id, metrica, periodo
    )
