"""API de Auditoría (Administrador y DBA; la bitácora del sistema, solo Administrador)."""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.identidad.dependencias import requerir_modulo
from app.modulos.auditoria import servicios
from app.modulos.auditoria.servicios import Filtros

router = APIRouter(prefix="/api/auditoria", tags=["auditoria"])
contexto = Depends(requerir_modulo("auditoria"))
Nombre = Literal["transacciones", "sistema", "excepciones"]


class NivelEntrada(BaseModel):
    nivel: str


def filtros(
    desde: date | None = None,
    hasta: date | None = None,
    usuario: str | None = Query(default=None, max_length=254),
    ambiente: str | None = None,
    tipo: str | None = Query(default=None, max_length=40),
    texto: str | None = Query(default=None, max_length=200),
) -> Filtros:
    return Filtros(desde, hasta, usuario, ambiente or None, tipo or None, texto)


@router.get("")
async def resumen(ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {
        "bitacoras": servicios.bitacoras_visibles(sesion),
        "totales": await servicios.totales(tenant, sesion),
        "nivel_excepciones": await servicios.nivel_excepciones(tenant),
        "puede_configurar_nivel": sesion.rol in servicios.ROLES_CONFIGURAN_NIVEL,
    }


@router.get("/{nombre}")
async def listar(
    nombre: Nombre, pagina: int = 1, f: Filtros = Depends(filtros), ctx=contexto
) -> dict:
    tenant, sesion = ctx
    return await servicios.listar(tenant, sesion, nombre, f, pagina)


@router.get("/{nombre}/registros/{registro_id}")
async def detalle(nombre: Nombre, registro_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.detalle(tenant, sesion, nombre, registro_id)


@router.get("/{nombre}/exportar")
async def exportar(
    nombre: Nombre,
    formato: Literal["csv", "pdf"],
    f: Filtros = Depends(filtros),
    ctx=contexto,
):
    tenant, sesion = ctx
    archivo = f"auditoria_{nombre}_{datetime.now():%Y%m%d_%H%M%S}.{formato}"
    cabeceras = {"Content-Disposition": f'attachment; filename="{archivo}"'}
    if formato == "pdf":
        contenido = await servicios.exportar_pdf(tenant, sesion, nombre, f)
        return Response(contenido, media_type="application/pdf", headers=cabeceras)
    lineas = await servicios.exportar_csv(tenant, sesion, nombre, f)
    return StreamingResponse(lineas, media_type="text/csv; charset=utf-8", headers=cabeceras)


@router.put("/excepciones/nivel")
async def cambiar_nivel(datos: NivelEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {"nivel": await servicios.cambiar_nivel_excepciones(tenant, sesion, datos.nivel)}
