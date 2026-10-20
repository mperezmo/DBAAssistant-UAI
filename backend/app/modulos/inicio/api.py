"""API del panel principal (todos los roles)."""

from fastapi import APIRouter, Depends

from app.identidad.dependencias import requerir_modulo
from app.modulos.inicio import servicios

router = APIRouter(prefix="/api/inicio", tags=["inicio"])
contexto = Depends(requerir_modulo("inicio"))


@router.get("")
async def resumen(ambiente: str = servicios.TODOS, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.resumen(tenant, sesion, ambiente)
