"""API REST del Chat IA (todos los roles; Consulta solo ejecuta SELECT)."""

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from app.identidad.dependencias import requerir_modulo
from app.modulos.chat_ia import servicios

router = APIRouter(prefix="/api/chat", tags=["chat"])
contexto = Depends(requerir_modulo("chat"))
LARGO_MAXIMO_SQL = 100_000


class PedidoEntrada(BaseModel):
    texto: str = Field(max_length=servicios.LARGO_MAXIMO_PEDIDO)
    hilo_id: str | None = None
    conexion_id: str | None = None


class SqlEntrada(BaseModel):
    sql: str = Field(max_length=LARGO_MAXIMO_SQL)
    base: str = Field(max_length=128)


class EjecucionEntrada(SqlEntrada):
    confirmar_advertencias: bool = False


@router.get("/conexiones")
async def conexiones(ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {"conexiones": await servicios.conexiones_disponibles(tenant, sesion)}


@router.get("/hilos")
async def hilos(ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {"hilos": await servicios.hilos(tenant, sesion)}


@router.get("/hilos/{hilo_id}")
async def hilo(hilo_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.hilo(tenant, sesion, hilo_id)


@router.delete("/hilos/{hilo_id}", status_code=204)
async def eliminar_hilo(hilo_id: str, ctx=contexto) -> None:
    tenant, sesion = ctx
    await servicios.eliminar_hilo(tenant, sesion, hilo_id)


@router.post("/mensajes")
async def enviar(datos: PedidoEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.enviar(tenant, sesion, datos.hilo_id, datos.conexion_id, datos.texto)


@router.post("/hilos/{hilo_id}/revisar")
async def revisar(hilo_id: str, datos: SqlEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.revisar_edicion(tenant, sesion, hilo_id, datos.base, datos.sql)


@router.post("/hilos/{hilo_id}/mensajes/{mensaje_id}/ejecutar")
async def ejecutar(hilo_id: str, mensaje_id: str, datos: EjecucionEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.ejecutar(
        tenant, sesion, hilo_id, mensaje_id, datos.sql, datos.base, datos.confirmar_advertencias
    )


@router.post("/hilos/{hilo_id}/mensajes/{mensaje_id}/sandbox")
async def sandbox(hilo_id: str, mensaje_id: str, datos: SqlEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.validar_en_sandbox(
        tenant, sesion, hilo_id, mensaje_id, datos.sql, datos.base
    )


@router.get("/hilos/{hilo_id}/mensajes/{mensaje_id}/csv")
async def exportar(hilo_id: str, mensaje_id: str, ctx=contexto) -> StreamingResponse:
    tenant, sesion = ctx
    filas, auditar = await servicios.exportar(tenant, sesion, hilo_id, mensaje_id)
    # La primera lectura se hace acá: si la consulta falla, se informa como error de la API
    # y no como un archivo cortado.
    encabezado = await run_in_threadpool(next, filas)

    exportadas = [0]

    def lineas():
        salida = io.StringIO()
        escritor = csv.writer(salida)
        salida.write("\ufeff")  # BOM: Excel reconoce los acentos
        escritor.writerow(encabezado)
        yield salida.getvalue()
        for fila in filas:
            salida.seek(0)
            salida.truncate()
            escritor.writerow(["" if v is None else v for v in fila])
            exportadas[0] += 1
            yield salida.getvalue()

    async def contenido():
        async for parte in iterate_in_threadpool(lineas()):
            yield parte
        await auditar(exportadas[0])

    nombre = f"consulta_{datetime.now():%Y%m%d_%H%M%S}.csv"
    return StreamingResponse(
        contenido(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
