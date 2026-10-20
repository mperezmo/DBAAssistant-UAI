"""API de suscripción a notificaciones push. Disponible para quien ve el módulo Alertas."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.identidad.dependencias import requerir_modulo
from app.modulos.web_push import servicios
from app.modulos.web_push.repositorios import Suscripcion

router = APIRouter(prefix="/api/push", tags=["push"])
contexto = Depends(requerir_modulo("alertas"))


class ClavesEntrada(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class SuscripcionEntrada(BaseModel):
    endpoint: str = Field(max_length=800)
    keys: ClavesEntrada


class BajaEntrada(BaseModel):
    endpoint: str = Field(max_length=800)


@router.get("")
async def estado(ctx=contexto) -> dict:
    tenant, sesion = ctx
    return servicios.estado(tenant, sesion)


@router.post("/suscripciones", status_code=204)
async def suscribir(datos: SuscripcionEntrada, request: Request, ctx=contexto) -> None:
    tenant, sesion = ctx
    await servicios.suscribir(
        tenant,
        sesion,
        Suscripcion(datos.endpoint, datos.keys.p256dh, datos.keys.auth),
        request.headers.get("user-agent"),
    )


@router.post("/suscripciones/baja", status_code=204)
async def desuscribir(datos: BajaEntrada, ctx=contexto) -> None:
    tenant, _ = ctx
    await servicios.desuscribir(tenant, datos.endpoint)
