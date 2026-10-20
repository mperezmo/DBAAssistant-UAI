"""Versiones del schema en la base MongoDB del tenant (colección ``schema_versiones``).

Cada versión guarda el snapshot completo de la base después del cambio, así se puede
comparar cualquier par de versiones sin reconstruir la historia.
"""

from datetime import UTC, datetime

from pymongo import DESCENDING

from app.datos import mongo

COLECCION = mongo.COLECCION_SCHEMA


async def guardar(base_mongodb: str, documento: dict) -> int:
    """Guarda una versión nueva con el número siguiente de esa conexión y base."""
    versiones = mongo.base(base_mongodb)[COLECCION]
    filtro = {k: documento[k] for k in ("tenant_id", "conexion_id", "base")}
    ultima = await versiones.find_one(filtro, {"version": 1}, sort=[("version", DESCENDING)])
    numero = (ultima["version"] if ultima else 0) + 1
    await versiones.insert_one({**documento, "version": numero, "ts": datetime.now(UTC)})
    return numero


async def listar(base_mongodb: str, tenant_id: str, conexion_id: str, base: str) -> list[dict]:
    cursor = (
        mongo.base(base_mongodb)[COLECCION]
        .find(
            {"tenant_id": tenant_id, "conexion_id": conexion_id, "base": base},
            {"objetos": 0, "cambios.antes": 0, "cambios.despues": 0},
        )
        .sort("version", DESCENDING)
    )
    return [d async for d in cursor]


async def obtener(
    base_mongodb: str, tenant_id: str, conexion_id: str, base: str, version: int
) -> dict | None:
    return await mongo.base(base_mongodb)[COLECCION].find_one(
        {"tenant_id": tenant_id, "conexion_id": conexion_id, "base": base, "version": version}
    )


async def bases_con_versiones(base_mongodb: str, tenant_id: str, conexion_id: str) -> list[str]:
    return sorted(
        await mongo.base(base_mongodb)[COLECCION].distinct(
            "base", {"tenant_id": tenant_id, "conexion_id": conexion_id}
        )
    )
