"""API REST de Contexto de Negocio.

Edita Administrador y DBA; Consulta, solo lectura. Los documentos de negocio los carga
cualquier rol y los gestiona quien los cargó, el Administrador o el DBA.
"""

from datetime import time
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, Field

from app.core.errores import ErrorDominio
from app.identidad.dependencias import requerir_modulo
from app.modulos.conexiones import servicios as conexiones
from app.modulos.contexto_negocio import esquema, servicios
from app.modulos.contexto_negocio.entidades import (
    MAXIMO_BYTES_DOCUMENTO,
    Documento,
    Regla,
    TablaSensible,
    Termino,
)

router = APIRouter(prefix="/api/contexto", tags=["contexto"])
contexto = Depends(requerir_modulo("contexto"))


class SensibleEntrada(BaseModel):
    conexion_id: str
    base: str = Field(max_length=128)
    tabla: str = Field(max_length=260)
    columnas: list[str] = Field(default_factory=list, max_length=100)
    roles_consulta: list[str] = Field(max_length=3)
    roles_ven_columnas: list[str] = Field(default_factory=list, max_length=3)
    doble_confirmacion: bool = False
    descripcion: str = Field(default="", max_length=500)


class TerminoEntrada(BaseModel):
    conexion_id: str
    base: str = Field(max_length=128)
    termino: str = Field(max_length=100)
    entidad: str = Field(max_length=260)
    sinonimos: list[str] = Field(default_factory=list, max_length=20)
    descripcion: str = Field(default="", max_length=1000)


class ReglaEntrada(BaseModel):
    descripcion: str = Field(max_length=500)
    acciones: list[Literal["SELECT", "INSERT", "UPDATE", "DELETE", "DDL"]] = Field(max_length=5)
    efecto: Literal["bloquear", "advertir"]
    prioridad: Literal[1, 2, 3] = 2
    conexion_id: str | None = None
    base: str | None = Field(default=None, max_length=128)
    ambientes: list[str] = Field(default_factory=list, max_length=3)
    tablas: list[str] = Field(default_factory=list, max_length=50)
    desde: time | None = None
    hasta: time | None = None
    activa: bool = True


class ResumenEntrada(BaseModel):
    resumen: str = Field(max_length=20_000)


@router.get("/conexiones")
async def listar_conexiones(ctx=contexto) -> dict:
    tenant, sesion = ctx
    return {
        "conexiones": [
            {"id": c.id, "alias": c.alias, "ambiente": c.ambiente}
            for c in await conexiones.listar(tenant)
        ],
        "puede_editar": servicios.puede_editar(sesion),
    }


@router.get("/conexiones/{conexion_id}/esquema")
async def ver_esquema(conexion_id: str, refrescar: bool = False, ctx=contexto) -> dict:
    tenant, _ = ctx
    return {"bases": await esquema.obtener(tenant, conexion_id, refrescar)}


@router.get("/conexiones/{conexion_id}")
async def listar(conexion_id: str, base: str | None = None, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.listar(tenant, conexion_id, base, sesion)


@router.post("/sensibles", status_code=201)
@router.put("/sensibles/{elemento_id}")
async def guardar_sensible(
    datos: SensibleEntrada, elemento_id: str | None = None, ctx=contexto
) -> dict:
    tenant, sesion = ctx
    return await servicios.guardar_sensible(
        tenant, sesion, elemento_id, TablaSensible(**datos.model_dump())
    )


@router.post("/terminos", status_code=201)
@router.put("/terminos/{elemento_id}")
async def guardar_termino(
    datos: TerminoEntrada, elemento_id: str | None = None, ctx=contexto
) -> dict:
    tenant, sesion = ctx
    valores = datos.model_dump()
    valores["sinonimos"] = tuple(valores["sinonimos"])
    return await servicios.guardar_termino(tenant, sesion, elemento_id, Termino(**valores))


@router.post("/reglas", status_code=201)
@router.put("/reglas/{elemento_id}")
async def guardar_regla(datos: ReglaEntrada, elemento_id: str | None = None, ctx=contexto) -> dict:
    tenant, sesion = ctx
    valores = datos.model_dump()
    for campo in ("acciones", "ambientes", "tablas"):
        valores[campo] = tuple(valores[campo])
    return await servicios.guardar_regla(tenant, sesion, elemento_id, Regla(**valores))


@router.post("/documentos", status_code=201)
async def cargar_documento(
    archivo: Annotated[UploadFile, File()],
    conexion_id: Annotated[str, Form()],
    base: Annotated[str, Form(max_length=128)],
    ctx=contexto,
) -> dict:
    tenant, sesion = ctx
    # Se lee un byte de más para detectar el exceso sin cargar archivos enormes.
    contenido = await archivo.read(MAXIMO_BYTES_DOCUMENTO + 1)
    if len(contenido) > MAXIMO_BYTES_DOCUMENTO:
        raise ErrorDominio("CTX_DOC_002")
    datos = Documento(conexion_id, base, archivo.filename or "")
    return await servicios.cargar_documento(tenant, sesion, datos, contenido)


@router.put("/documentos/{documento_id}/resumen")
async def guardar_resumen(documento_id: str, datos: ResumenEntrada, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.guardar_resumen(tenant, sesion, documento_id, datos.resumen)


@router.post("/documentos/{documento_id}/resumen")
async def regenerar_resumen(documento_id: str, ctx=contexto) -> dict:
    tenant, sesion = ctx
    return await servicios.regenerar_resumen(tenant, sesion, documento_id)


@router.post("/documentos/{documento_id}/propuestas/{propuesta_id}/{decision}")
async def resolver_propuesta(
    documento_id: str,
    propuesta_id: str,
    decision: Literal["aceptar", "descartar"],
    ctx=contexto,
) -> dict:
    tenant, sesion = ctx
    resolver = (
        servicios.aceptar_propuesta if decision == "aceptar" else servicios.descartar_propuesta
    )
    return await resolver(tenant, sesion, documento_id, propuesta_id)


@router.get("/documentos/{documento_id}/archivo")
async def descargar_documento(documento_id: str, ctx=contexto) -> Response:
    tenant, _ = ctx
    nombre, contenido = await servicios.descargar_documento(tenant, documento_id)
    return Response(
        contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(nombre)}"},
    )


@router.delete("/{tipo}/{elemento_id}", status_code=204)
async def eliminar(
    tipo: Literal["sensibles", "terminos", "reglas", "documentos"], elemento_id: str, ctx=contexto
) -> None:
    tenant, sesion = ctx
    singular = {
        "sensibles": "sensible",
        "terminos": "termino",
        "reglas": "regla",
        "documentos": "documento",
    }[tipo]
    await servicios.eliminar(tenant, sesion, singular, elemento_id)
