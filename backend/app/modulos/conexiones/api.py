"""API REST de conexiones a instancias SQL Server (Administrador y DBA)."""

from datetime import time
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.identidad.dependencias import requerir_modulo
from app.modulos.conexiones import servicios
from app.modulos.conexiones.entidades import ZONA_HORARIA_POR_DEFECTO, Conexion, DatosConexion

router = APIRouter(prefix="/api/conexiones", tags=["conexiones"])
contexto = Depends(requerir_modulo("conexiones"))


class ConexionEntrada(BaseModel):
    alias: str
    host: str
    puerto: int | None = Field(default=None, ge=1, le=65535)
    instancia: str | None = None
    ambiente: Literal["DEV", "TESTING", "PROD"]
    usuario_sql: str
    password_sql: str | None = None
    winrm_usuario: str | None = None
    winrm_password: str | None = None
    winrm_puerto: int | None = Field(default=None, ge=1, le=65535)
    winrm_https: bool = False
    ventana_inicio: time | None = None
    ventana_fin: time | None = None
    zona_horaria: str = Field(default=ZONA_HORARIA_POR_DEFECTO, max_length=64)

    def a_datos(self) -> DatosConexion:
        return DatosConexion(**self.model_dump())


class PruebaEntrada(ConexionEntrada):
    # Al probar una conexión guardada sin reescribir la contraseña se usa la almacenada.
    id: str | None = None

    def a_datos(self) -> DatosConexion:
        return DatosConexion(**self.model_dump(exclude={"id"}))


def _a_json(c: Conexion) -> dict:
    """Representación pública: nunca incluye contraseñas, ni cifradas."""
    return {
        "id": c.id,
        "alias": c.alias,
        "host": c.host,
        "puerto": c.puerto,
        "instancia": c.instancia,
        "ambiente": c.ambiente,
        "usuario_sql": c.usuario_sql,
        "winrm_usuario": c.winrm_usuario,
        "winrm_puerto": c.winrm_puerto,
        "winrm_https": c.winrm_https,
        "tiene_winrm": c.tiene_winrm,
        "ventana_inicio": c.ventana_inicio.strftime("%H:%M") if c.ventana_inicio else None,
        "ventana_fin": c.ventana_fin.strftime("%H:%M") if c.ventana_fin else None,
        "zona_horaria": c.zona_horaria,
    }


@router.get("")
async def listar(ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"conexiones": [_a_json(c) for c in await servicios.listar(tenant)]}


@router.post("", status_code=201)
async def crear(datos: ConexionEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return _a_json(await servicios.crear(tenant, sesion, datos.a_datos()))


@router.patch("/{conexion_id}")
async def modificar(conexion_id: str, datos: ConexionEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return _a_json(await servicios.modificar(tenant, sesion, conexion_id, datos.a_datos()))


@router.delete("/{conexion_id}", status_code=204)
async def eliminar(conexion_id: str, ctx=contexto) -> None:
    tenant, sesion = ctx
    await servicios.eliminar(tenant, sesion, conexion_id)


@router.post("/probar")
async def probar(datos: PruebaEntrada, ctx=contexto) -> dict:
    tenant, _ = ctx
    resultado = await servicios.probar(tenant, datos.a_datos(), datos.id)
    return {"version": resultado.version, "edicion": resultado.edicion, "bases": resultado.bases}


@router.post("/probar-winrm")
async def probar_winrm(datos: PruebaEntrada, ctx=contexto) -> dict:
    tenant, _ = ctx
    servicios_sql = await servicios.probar_winrm(tenant, datos.a_datos(), datos.id)
    return {
        "servicios": [
            {"nombre": s.nombre, "nombre_visible": s.nombre_visible, "estado": s.estado}
            for s in servicios_sql
        ]
    }
