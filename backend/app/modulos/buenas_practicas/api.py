"""API de Buenas Prácticas (Administrador y DBA; aplica y define reglas el DBA)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.identidad.dependencias import requerir_modulo
from app.modulos.buenas_practicas import servicios
from app.modulos.buenas_practicas.chequeos import TIPOS

router = APIRouter(prefix="/api/buenas-practicas", tags=["buenas_practicas"])
contexto = Depends(requerir_modulo("buenas_practicas"))


class AnalisisEntrada(BaseModel):
    conexion_id: str
    base: str = Field(max_length=128)
    tipos: list[str] = Field(max_length=len(TIPOS))


class ReglaEntrada(BaseModel):
    texto: str = Field(max_length=servicios.LARGO_MAXIMO_REGLA)
    activa: bool = True


@router.get("")
async def resumen(conexion_id: str | None = None, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {
        "informes": await servicios.informes(tenant, sesion, conexion_id),
        "reglas": await servicios.reglas(tenant),
        "tipos": list(TIPOS),
        "permisos": servicios.permisos(sesion),
    }


@router.get("/conexiones")
async def conexiones_disponibles(ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {"conexiones": await servicios.conexiones_disponibles(tenant, sesion)}


@router.get("/conexiones/{conexion_id}/bases")
async def bases(conexion_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {"bases": await servicios.bases(tenant, sesion, conexion_id)}


@router.post("/informes", status_code=202)
async def solicitar(datos: AnalisisEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.solicitar(tenant, sesion, datos.conexion_id, datos.base, datos.tipos)


@router.get("/informes/{informe_id}")
async def informe(informe_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.informe(tenant, sesion, informe_id)


@router.post("/informes/{informe_id}/sugerencias/{sugerencia_id}/sandbox")
async def validar(informe_id: str, sugerencia_id: int, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.validar_en_sandbox(tenant, sesion, informe_id, sugerencia_id)


@router.post("/reglas", status_code=201)
@router.put("/reglas/{regla_id}")
async def guardar_regla(datos: ReglaEntrada, regla_id: str | None = None, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.guardar_regla(tenant, sesion, regla_id, datos.texto, datos.activa)


@router.delete("/reglas/{regla_id}", status_code=204)
async def eliminar_regla(regla_id: str, ctx=contexto) -> None:
    tenant, sesion = ctx
    await servicios.eliminar_regla(tenant, sesion, regla_id)
