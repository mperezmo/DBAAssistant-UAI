"""Persistencia del contexto de negocio en la base MongoDB del tenant.

Un único documento por elemento, distinguido por ``tipo`` (sensible, termino o regla).
"""

from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId
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
