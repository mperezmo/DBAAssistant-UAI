"""API REST de workarounds (Administrador y DBA; el rol Consulta no ve el módulo)."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.identidad.dependencias import requerir_modulo
from app.modulos.workarounds import servicios
from app.modulos.workarounds.entidades import Propio

router = APIRouter(prefix="/api/workarounds", tags=["workarounds"])
contexto = Depends(requerir_modulo("workarounds"))


class PropioEntrada(BaseModel):
    nombre: str = Field(max_length=100)
    descripcion: str = Field(max_length=500)
    categoria: Literal["performance", "espacio", "mantenimiento", "servicio"]
    criticidad: Literal["info", "warning", "critical"]
    ambientes: list[str] = Field(max_length=3)
    roles: list[str] = Field(max_length=2)
    transaccional: bool = True
    condicion: str | None = Field(default=None, max_length=300)
    pasos: list[str] = Field(max_length=20)
    # Conexión y base contra las que se valida la sintaxis antes de guardar.
    validar_conexion_id: str
    validar_base: str | None = None

    def a_propio(self) -> Propio:
        return Propio(
            id="",
            tenant_id="",
            nombre=self.nombre,
            descripcion=self.descripcion,
            categoria=self.categoria,
            criticidad=self.criticidad,
            ambientes=tuple(self.ambientes),
            roles=tuple(self.roles),
            transaccional=self.transaccional,
            condicion=self.condicion,
            version_actual=0,
            version_aprobada=None,
            pasos=tuple(self.pasos),
        )


class EjecucionEntrada(BaseModel):
    conexion_id: str
    base: str | None = Field(default=None, max_length=128)
    parametros: dict = Field(default_factory=dict)
    alerta_id: int | None = None
    origen: Literal["web", "mobile"] = "web"


class ValidacionEntrada(BaseModel):
    conexion_id: str
    base: str = Field(max_length=128)


class AutorizacionEntrada(BaseModel):
    conexion_id: str
    motivo: str = Field(max_length=500)


class ResolucionEntrada(BaseModel):
    aprobar: bool


class AutomatizacionEntrada(BaseModel):
    evento: Literal["log", "instancia"]
    workaround: str = Field(max_length=60)
    conexion_id: str | None = None


@router.get("")
async def biblioteca(ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {
        "workarounds": await servicios.biblioteca(tenant),
        "permisos": servicios.permisos(sesion),
    }


@router.get("/ejecuciones")
async def ejecuciones(ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"ejecuciones": await servicios.ejecuciones(tenant)}


@router.get("/ejecuciones/{ejecucion_id}")
async def ejecucion(ejecucion_id: int, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await servicios.obtener_ejecucion(tenant, ejecucion_id)


@router.get("/conexiones/{conexion_id}/bases")
async def bases(conexion_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"bases": await servicios.bases(tenant, conexion_id)}


@router.get("/autorizaciones")
async def autorizaciones(ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"autorizaciones": await servicios.autorizaciones(tenant)}


@router.post("/autorizaciones/{autorizacion_id}", status_code=204)
async def resolver_autorizacion(
    autorizacion_id: str, datos: ResolucionEntrada, ctx=contexto
) -> None:
    tenant, sesion = ctx
    await servicios.resolver_autorizacion(tenant, sesion, autorizacion_id, datos.aprobar)


@router.get("/automatizaciones")
async def automatizaciones(ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"automatizaciones": await servicios.automatizaciones(tenant)}


@router.post("/automatizaciones", status_code=201)
async def crear_automatizacion(datos: AutomatizacionEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.crear_automatizacion(
        tenant, sesion, datos.evento, datos.workaround, datos.conexion_id
    )


@router.delete("/automatizaciones/{automatizacion_id}", status_code=204)
async def eliminar_automatizacion(automatizacion_id: str, ctx=contexto) -> None:
    tenant, sesion = ctx
    await servicios.eliminar_automatizacion(tenant, sesion, automatizacion_id)


@router.post("", status_code=201)
async def crear(datos: PropioEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.guardar_propio(
        tenant, sesion, None, datos.a_propio(), (datos.validar_conexion_id, datos.validar_base)
    )


@router.get("/{workaround_id}")
async def detalle(workaround_id: str, ctx=contexto) -> dict:
    tenant, _ = ctx
    return await servicios.detalle(tenant, workaround_id)


@router.put("/{workaround_id}")
async def modificar(workaround_id: str, datos: PropioEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.guardar_propio(
        tenant,
        sesion,
        workaround_id,
        datos.a_propio(),
        (datos.validar_conexion_id, datos.validar_base),
    )


@router.delete("/{workaround_id}", status_code=204)
async def eliminar(workaround_id: str, ctx=contexto) -> None:
    tenant, sesion = ctx
    await servicios.eliminar_propio(tenant, sesion, workaround_id)


@router.post("/{workaround_id}/validar")
async def validar(workaround_id: str, datos: ValidacionEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.validar_en_sandbox(
        tenant, sesion, workaround_id, datos.conexion_id, datos.base
    )


@router.post("/{workaround_id}/diagnostico")
async def diagnostico(workaround_id: str, datos: EjecucionEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.diagnostico(
        tenant, sesion, workaround_id, datos.conexion_id, datos.base, datos.parametros
    )


@router.post("/{workaround_id}/ejecutar", status_code=202)
async def ejecutar(workaround_id: str, datos: EjecucionEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    ejecucion_id = await servicios.ejecutar(
        tenant,
        sesion,
        workaround_id,
        datos.conexion_id,
        datos.base,
        datos.parametros,
        datos.origen,
        datos.alerta_id,
    )
    return {"ejecucion_id": ejecucion_id}


@router.post("/{workaround_id}/autorizaciones", status_code=204)
async def solicitar_autorizacion(
    workaround_id: str, datos: AutorizacionEntrada, ctx=contexto
) -> None:
    tenant, sesion = ctx
    await servicios.solicitar_autorizacion(
        tenant, sesion, workaround_id, datos.conexion_id, datos.motivo
    )
