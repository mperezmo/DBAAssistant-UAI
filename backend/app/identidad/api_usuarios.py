"""API REST de gestión de usuarios (Panel Admin)."""

import math
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import obtener_configuracion
from app.identidad import usuarios
from app.identidad.dependencias import requerir_modulo
from app.identidad.entidades import DIAS_GRACIA_BAJA, Usuario

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])
contexto = Depends(requerir_modulo("admin"))
# Preferencias del propio usuario: cualquier rol (todos ven el Inicio).
router_perfil = APIRouter(prefix="/api/perfil", tags=["perfil"])
contexto_propio = Depends(requerir_modulo("inicio"))

Rol = Literal["ADMINISTRADOR", "DBA", "CONSULTA"]
Ambiente = Literal["DEV", "TESTING", "PROD"]


class UsuarioNuevo(BaseModel):
    email: str
    nombre: str | None = None
    rol: Rol
    ambientes: list[Ambiente] = Field(default_factory=list)


class UsuarioCambios(BaseModel):
    nombre: str | None = None
    rol: Rol
    ambientes: list[Ambiente] = Field(default_factory=list)


def _a_json(usuario: Usuario) -> dict:
    dias_restantes = None
    if usuario.fecha_baja:
        vence = usuario.fecha_baja.replace(tzinfo=UTC) + timedelta(days=DIAS_GRACIA_BAJA)
        segundos = (vence - datetime.now(UTC)).total_seconds()
        dias_restantes = max(0, math.ceil(segundos / 86_400))
    return {
        "id": usuario.id,
        "email": usuario.email,
        "nombre": usuario.nombre,
        "rol": usuario.rol,
        "estado": usuario.estado,
        "ambientes": list(usuario.ambientes),
        "ultimo_ingreso": usuario.ultimo_ingreso.isoformat() if usuario.ultimo_ingreso else None,
        "dias_para_purga": dias_restantes,
    }


@router.get("")
async def listar(bajas: bool = False, ctx=contexto) -> dict:
    tenant, sesion = ctx
    lista = await usuarios.listar(tenant, sesion, bajas)
    return {
        "usuarios": [_a_json(u) for u in lista],
        "direccion_acceso": f"https://{tenant.slug}.{obtener_configuracion().dominio_base}",
    }


@router.post("", status_code=201)
async def crear(datos: UsuarioNuevo, ctx=contexto) -> dict:
    tenant, sesion = ctx
    usuario = await usuarios.crear(
        tenant,
        sesion,
        usuarios.DatosUsuario(datos.email, datos.nombre, datos.rol, tuple(datos.ambientes)),
    )
    return _a_json(usuario)


@router.patch("/{usuario_id}")
async def modificar(usuario_id: str, datos: UsuarioCambios, ctx=contexto) -> dict:
    tenant, sesion = ctx
    usuario = await usuarios.modificar(
        tenant,
        sesion,
        usuario_id,
        usuarios.DatosUsuario("", datos.nombre, datos.rol, tuple(datos.ambientes)),
    )
    return _a_json(usuario)


@router.delete("/{usuario_id}", status_code=204)
async def dar_baja(usuario_id: str, ctx=contexto) -> None:
    tenant, sesion = ctx
    await usuarios.dar_baja(tenant, sesion, usuario_id)


@router.post("/{usuario_id}/rehabilitar")
async def rehabilitar(usuario_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return _a_json(await usuarios.rehabilitar(tenant, sesion, usuario_id))


class Idioma(BaseModel):
    idioma: Literal["es", "en"]


@router_perfil.put("/idioma", status_code=204)
async def cambiar_idioma(datos: Idioma, ctx=contexto_propio) -> None:
    tenant, sesion = ctx
    await usuarios.cambiar_idioma(tenant, sesion, datos.idioma)
