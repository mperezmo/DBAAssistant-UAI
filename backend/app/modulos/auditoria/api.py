"""API de Auditoría: la bitácora de transacciones (Administrador y DBA).

Las bitácoras del sistema y de excepciones se consultan desde Soporte (``/api/soporte``),
con el mismo motor de consulta y exportación.
"""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse

from app.identidad.dependencias import requerir_modulo
from app.modulos.auditoria import servicios
from app.modulos.auditoria.servicios import Filtros

router = APIRouter(prefix="/api/auditoria", tags=["auditoria"])
contexto = Depends(requerir_modulo("auditoria"))
Nombre = Literal["transacciones"]


def filtros(
    desde: date | None = None,
    hasta: date | None = None,
    usuario: str | None = Query(default=None, max_length=254),
    ambiente: str | None = None,
    tipo: str | None = Query(default=None, max_length=40),
    texto: str | None = Query(default=None, max_length=200),
    origen: str | None = Query(default=None, max_length=20),
) -> Filtros:
    return Filtros(desde, hasta, usuario, ambiente or None, tipo or None, texto, origen or None)


@router.get("")
async def resumen(ctx=contexto) -> dict:
    tenant, sesion = ctx
    grupo = servicios.BITACORAS_AUDITORIA
    return {
        "bitacoras": servicios.bitacoras_visibles(sesion, grupo),
        "totales": await servicios.totales(tenant, sesion, grupo),
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
    return await respuesta_exportacion(tenant, sesion, nombre, formato, f)


async def respuesta_exportacion(tenant, sesion, nombre: str, formato: str, f: Filtros):
    """Descarga de una bitácora (la usa también Soporte)."""
    archivo = f"auditoria_{nombre}_{datetime.now():%Y%m%d_%H%M%S}.{formato}"
    cabeceras = {"Content-Disposition": f'attachment; filename="{archivo}"'}
    if formato == "pdf":
        contenido = await servicios.exportar_pdf(tenant, sesion, nombre, f)
        return Response(contenido, media_type="application/pdf", headers=cabeceras)
    lineas = await servicios.exportar_csv(tenant, sesion, nombre, f)
    return StreamingResponse(lineas, media_type="text/csv; charset=utf-8", headers=cabeceras)
