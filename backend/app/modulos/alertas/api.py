"""API REST de alertas. Todos los roles con el módulo ven; atiende el DBA; configuran
Administrador y DBA."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.identidad.dependencias import requerir_modulo
from app.modulos.alertas import servicios
from app.modulos.alertas.entidades import (
    SOSTENIDA_POR_DEFECTO_SEGUNDOS,
    Alerta,
    Desestimada,
    Regla,
)
from app.modulos.workarounds import integrados

router = APIRouter(prefix="/api/alertas", tags=["alertas"])
contexto = Depends(requerir_modulo("alertas"))


class ReglaEntrada(BaseModel):
    nombre: str = Field(max_length=100)
    metrica: str
    operador: Literal[">", ">=", "<", "<="]
    valor: float
    severidad: Literal["info", "warning", "critical"]
    conexion_id: str | None = None
    sostenida_segundos: int = SOSTENIDA_POR_DEFECTO_SEGUNDOS
    destinatarios: list[str] = Field(max_length=3)
    push: bool = False
    email: bool = False
    activa: bool = True

    def a_regla(self) -> Regla:
        datos = self.model_dump()
        return Regla(id="", tenant_id="", **{**datos, "destinatarios": tuple(self.destinatarios)})


class DesestimacionEntrada(BaseModel):
    motivo: str = Field(max_length=500)
    # Sin fecha: la desestimación no vence.
    hasta: datetime | None = None


class AtencionEntrada(BaseModel):
    accion: Literal["manual", "falsa_alarma"]
    comentario: str = Field(max_length=1000)


def _fecha(valor) -> str | None:
    return valor.isoformat() + "Z" if valor else None


def _sugerido(a: Alerta) -> dict | None:
    sugerido = integrados.sugerido_para(a.clave)
    if sugerido is None:
        return None
    workaround_id, parametros = sugerido
    i = integrados.CATALOGO[workaround_id]
    return {"id": i.id, "codigo": i.codigo, "nombre": i.nombre, "parametros": parametros}


def _alerta(a: Alerta) -> dict:
    return {
        **{k: v for k, v in a.__dict__.items() if not k.endswith("_en")},
        **{k: _fecha(v) for k, v in a.__dict__.items() if k.endswith("_en")},
        "workaround_sugerido": _sugerido(a),
    }


def _regla(r: Regla) -> dict:
    return {**r.__dict__, "destinatarios": list(r.destinatarios)}


@router.get("")
async def listar(estado: str = "activa", ctx=contexto) -> dict:
    tenant, sesion = ctx
    alertas = await servicios.listar(tenant, estado)
    return {"alertas": [_alerta(a) for a in alertas], "permisos": servicios.permisos(sesion)}


@router.get("/resumen")
async def resumen(ctx=contexto) -> dict:
    tenant, _ = ctx
    return await servicios.resumen(tenant)


def _desestimada(d: Desestimada) -> dict:
    return {**d.__dict__, "hasta": _fecha(d.hasta), "creada_en": _fecha(d.creada_en)}


@router.get("/desestimadas")
async def listar_desestimadas(ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"desestimadas": [_desestimada(d) for d in await servicios.listar_desestimadas(tenant)]}


@router.post("/desestimadas/{desestimada_id}/reactivar", status_code=204)
async def reactivar(desestimada_id: str, ctx=contexto) -> None:
    tenant, sesion = ctx
    await servicios.reactivar(tenant, sesion, desestimada_id)


@router.get("/reglas")
async def listar_reglas(ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"reglas": [_regla(r) for r in await servicios.listar_reglas(tenant)]}


@router.post("/reglas", status_code=201)
async def crear_regla(datos: ReglaEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return _regla(await servicios.crear_regla(tenant, sesion, datos.a_regla()))


@router.put("/reglas/{regla_id}")
async def modificar_regla(regla_id: str, datos: ReglaEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return _regla(await servicios.modificar_regla(tenant, sesion, regla_id, datos.a_regla()))


@router.delete("/reglas/{regla_id}")
async def eliminar_regla(regla_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {"alertas_cerradas": await servicios.eliminar_regla(tenant, sesion, regla_id)}


@router.get("/{alerta_id}")
async def obtener(alerta_id: int, ctx=contexto) -> dict:
    tenant, _ = ctx
    return _alerta(await servicios.obtener(tenant, alerta_id))


@router.post("/{alerta_id}/desestimar")
async def desestimar(alerta_id: int, datos: DesestimacionEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return _alerta(await servicios.desestimar(tenant, sesion, alerta_id, datos.motivo, datos.hasta))


@router.post("/{alerta_id}/atender")
async def atender(alerta_id: int, datos: AtencionEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return _alerta(
        await servicios.atender(tenant, sesion, alerta_id, datos.accion, datos.comentario)
    )
