"""Informes y reglas de buenas prácticas en la base MongoDB del tenant."""

from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId

from app.datos import mongo

INFORMES = mongo.COLECCION_INFORMES
REGLAS = mongo.COLECCION_REGLAS_BP
MAXIMO_INFORMES_LISTADOS = 50


def _id(valor: str) -> ObjectId | None:
    try:
        return ObjectId(valor)
    except InvalidId, TypeError:
        return None


async def crear_informe(base_mongodb: str, documento: dict) -> str:
    resultado = await mongo.base(base_mongodb)[INFORMES].insert_one(
        {**documento, "ts": datetime.now(UTC)}
    )
    return str(resultado.inserted_id)


async def actualizar_informe(base_mongodb: str, informe_id: str, campos: dict) -> None:
    await mongo.base(base_mongodb)[INFORMES].update_one(
        {"_id": ObjectId(informe_id)}, {"$set": campos}
    )


async def obtener_informe(base_mongodb: str, tenant_id: str, informe_id: str) -> dict | None:
    oid = _id(informe_id)
    if oid is None:
        return None
    return await mongo.base(base_mongodb)[INFORMES].find_one({"_id": oid, "tenant_id": tenant_id})


async def listar_informes(base_mongodb: str, tenant_id: str, conexion_id: str | None) -> list[dict]:
    filtro = {"tenant_id": tenant_id, **({"conexion_id": conexion_id} if conexion_id else {})}
    cursor = (
        mongo.base(base_mongodb)[INFORMES]
        .find(filtro, {"hallazgos": 0, "sugerencias.sql": 0, "sugerencias.descripcion": 0})
        .sort("ts", -1)
        .limit(MAXIMO_INFORMES_LISTADOS)
    )
    return [d async for d in cursor]


async def listar_reglas(base_mongodb: str, tenant_id: str) -> list[dict]:
    cursor = mongo.base(base_mongodb)[REGLAS].find({"tenant_id": tenant_id}).sort("_id", 1)
    return [d async for d in cursor]


async def guardar_regla(
    base_mongodb: str, tenant_id: str, regla_id: str | None, datos: dict
) -> str | None:
    coleccion = mongo.base(base_mongodb)[REGLAS]
    if regla_id is None:
        return str((await coleccion.insert_one({**datos, "tenant_id": tenant_id})).inserted_id)
    oid = _id(regla_id)
    if oid is None:
        return None
    resultado = await coleccion.update_one({"_id": oid, "tenant_id": tenant_id}, {"$set": datos})
    return regla_id if resultado.matched_count else None


async def eliminar_regla(base_mongodb: str, tenant_id: str, regla_id: str) -> bool:
    oid = _id(regla_id)
    if oid is None:
        return False
    resultado = await mongo.base(base_mongodb)[REGLAS].delete_one(
        {"_id": oid, "tenant_id": tenant_id}
    )
    return resultado.deleted_count == 1
