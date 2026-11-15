"""Persistencia del contexto de negocio en la base MongoDB del tenant.

Un único documento por elemento, distinguido por ``tipo`` (sensible, termino, regla o
documento). El PDF de cada documento de negocio y su conversión a Markdown van en GridFS
(bucket ``documentos``).
"""

import contextlib
from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId
from gridfs.errors import NoFile
from pymongo.errors import DuplicateKeyError

from app.datos import mongo

COLECCION = mongo.COLECCION_CONTEXTO


class Duplicado(Exception):
    """Ya existe un elemento con la misma clave en esa base."""


def _id(valor: str) -> ObjectId | None:
    try:
        return ObjectId(valor)
    except InvalidId, TypeError:
        return None


def _salida(documento: dict) -> dict:
    salida = {k: v for k, v in documento.items() if k not in ("_id", "tenant_id", "clave")}
    salida["id"] = str(documento["_id"])
    if isinstance(documento.get("actualizado_en"), datetime):
        salida["actualizado_en"] = documento["actualizado_en"].replace(tzinfo=UTC).isoformat()
    return salida


async def listar(base_mongodb: str, tenant_id: str, filtro: dict) -> list[dict]:
    cursor = (
        mongo.base(base_mongodb)[COLECCION]
        .find({"tenant_id": tenant_id, **filtro})
        .sort([("prioridad", 1), ("clave", 1), ("_id", 1)])
    )
    return [_salida(d) async for d in cursor]


async def obtener(base_mongodb: str, tenant_id: str, tipo: str, elemento_id: str) -> dict | None:
    oid = _id(elemento_id)
    if oid is None:
        return None
    documento = await mongo.base(base_mongodb)[COLECCION].find_one(
        {"_id": oid, "tenant_id": tenant_id, "tipo": tipo}
    )
    return _salida(documento) if documento else None


async def guardar(
    base_mongodb: str, tenant_id: str, elemento_id: str | None, documento: dict
) -> str:
    """Alta (sin id) o reemplazo. Lanza ``Duplicado`` si choca con el índice único."""
    datos = {**documento, "tenant_id": tenant_id, "actualizado_en": datetime.now(UTC)}
    coleccion = mongo.base(base_mongodb)[COLECCION]
    try:
        if elemento_id is None:
            return str((await coleccion.insert_one(datos)).inserted_id)
        await coleccion.replace_one({"_id": ObjectId(elemento_id), "tenant_id": tenant_id}, datos)
        return elemento_id
    except DuplicateKeyError as error:
        raise Duplicado from error


async def eliminar(base_mongodb: str, tenant_id: str, tipo: str, elemento_id: str) -> bool:
    oid = _id(elemento_id)
    if oid is None:
        return False
    resultado = await mongo.base(base_mongodb)[COLECCION].delete_one(
        {"_id": oid, "tenant_id": tenant_id, "tipo": tipo}
    )
    return resultado.deleted_count == 1


async def resolver_propuesta(
    base_mongodb: str,
    tenant_id: str,
    documento_id: str,
    propuesta_id: str,
    desde: str,
    campos: dict,
) -> bool:
    """Cambia una propuesta de un documento solo si sigue en el estado ``desde``: dos
    usuarios que aceptan la misma propuesta a la vez no crean el elemento dos veces."""
    oid = _id(documento_id)
    if oid is None:
        return False
    resultado = await mongo.base(base_mongodb)[COLECCION].update_one(
        {
            "_id": oid,
            "tenant_id": tenant_id,
            "tipo": "documento",
            "propuestas": {"$elemMatch": {"id": propuesta_id, "estado": desde}},
        },
        {"$set": {f"propuestas.$.{k}": v for k, v in campos.items()}},
    )
    return resultado.modified_count == 1


async def actualizar(
    base_mongodb: str, tenant_id: str, tipo: str, elemento_id: str, campos: dict
) -> None:
    oid = _id(elemento_id)
    if oid is None:
        return
    await mongo.base(base_mongodb)[COLECCION].update_one(
        {"_id": oid, "tenant_id": tenant_id, "tipo": tipo},
        {"$set": {**campos, "actualizado_en": datetime.now(UTC)}},
    )


# --- Archivos de los documentos de negocio (GridFS) ---------------------------------


async def guardar_archivo(
    base_mongodb: str, tenant_id: str, nombre: str, contenido: bytes, tipo: str
) -> str:
    archivo_id = await mongo.archivos(base_mongodb).upload_from_stream(
        nombre, contenido, metadata={"tenant_id": tenant_id, "tipo": tipo}
    )
    return str(archivo_id)


async def leer_archivo(base_mongodb: str, tenant_id: str, archivo_id: str) -> bytes | None:
    oid = _id(archivo_id)
    if oid is None:
        return None
    try:
        flujo = await mongo.archivos(base_mongodb).open_download_stream(oid)
    except NoFile:
        return None
    if (flujo.metadata or {}).get("tenant_id") != tenant_id:
        return None
    return await flujo.read()


async def eliminar_archivo(base_mongodb: str, archivo_id: str) -> None:
    oid = _id(archivo_id)
    if oid is None:
        return
    with contextlib.suppress(NoFile):  # ya no estaba: no hay nada que borrar
        await mongo.archivos(base_mongodb).delete(oid)
