"""API del Histórico de Schema (todos los roles, solo lectura)."""

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.identidad.dependencias import requerir_modulo
from app.modulos.conexiones import servicios as conexiones
from app.modulos.contexto_negocio import esquema
from app.modulos.historico_schema import servicios

router = APIRouter(prefix="/api/schema", tags=["schema"])
contexto = Depends(requerir_modulo("schema"))


@router.get("/conexiones")
async def listar_conexiones(ctx=contexto) -> dict:
    tenant, _ = ctx
    return {
        "conexiones": [
            {"id": c.id, "alias": c.alias, "ambiente": c.ambiente}
            for c in await conexiones.listar(tenant)
        ]
    }


@router.get("/conexiones/{conexion_id}/bases")
async def bases(conexion_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    actuales = await esquema.obtener(tenant, conexion_id)
    con_versiones = await servicios.repositorios.bases_con_versiones(
        tenant.base_mongodb, str(tenant.id), conexion_id
    )
    # También las bases que ya no existen pero tienen historia.
    return {"bases": sorted(set(actuales) | set(con_versiones), key=str.lower)}


@router.get("/conexiones/{conexion_id}/bases/{base}/versiones")
async def versiones(conexion_id: str, base: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await servicios.versiones(tenant, conexion_id, base)


@router.get("/conexiones/{conexion_id}/bases/{base}/versiones/{version}")
async def detalle(conexion_id: str, base: str, version: int, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await servicios.detalle(tenant, conexion_id, base, version)


@router.get("/conexiones/{conexion_id}/bases/{base}/comparar")
async def comparar(
    conexion_id: str, base: str, desde: int, hasta: str = servicios.ACTUAL, ctx=contexto
) -> dict:
    tenant, _ = ctx
    destino = hasta if hasta == servicios.ACTUAL else int(hasta)
    return await servicios.comparar(tenant, conexion_id, base, desde, destino)


@router.get("/conexiones/{conexion_id}/bases/{base}/comparar.sql")
async def exportar_comparacion(
    conexion_id: str, base: str, desde: int, hasta: str = servicios.ACTUAL, ctx=contexto
) -> PlainTextResponse:
    tenant, _ = ctx
    destino = hasta if hasta == servicios.ACTUAL else int(hasta)
    comparacion = await servicios.comparar(tenant, conexion_id, base, desde, destino)
    archivo = f"schema_{base}_v{desde}_{hasta}.sql"
    return PlainTextResponse(
        servicios.como_script(comparacion),
        headers={"Content-Disposition": f'attachment; filename="{archivo}"'},
    )
