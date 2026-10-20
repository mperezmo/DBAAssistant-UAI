"""Persistencia de la bitácora de auditoría en la base MongoDB del tenant."""

from app.datos import mongo
from app.modulos.auditoria.entidades import EventoAuditoria


async def registrar(base_mongodb: str, evento: EventoAuditoria) -> None:
    await mongo.base(base_mongodb)["bitacora_auditoria"].insert_one(evento.a_documento())


async def registrar_transaccion(base_mongodb: str, evento: EventoAuditoria) -> None:
    """Operaciones ejecutadas sobre las instancias (workarounds, promociones a PROD)."""
    await mongo.base(base_mongodb)["bitacora_transacciones"].insert_one(evento.a_documento())


# --- Consulta de las bitácoras (CU-000-009) -----------------------------------------


async def consultar(
    base_mongodb: str, coleccion: str, filtro: dict, salto: int, cantidad: int
) -> tuple[list[dict], int]:
    """Una página de la bitácora, de la más reciente a la más antigua, y el total."""
    bitacora = mongo.base(base_mongodb)[coleccion]
    total = await bitacora.count_documents(filtro)
    cursor = bitacora.find(filtro).sort("ts", -1).skip(salto).limit(cantidad)
    return [d async for d in cursor], total


async def obtener(base_mongodb: str, coleccion: str, filtro: dict) -> dict | None:
    return await mongo.base(base_mongodb)[coleccion].find_one(filtro)


async def contar(base_mongodb: str, coleccion: str, filtro: dict) -> int:
    return await mongo.base(base_mongodb)[coleccion].count_documents(filtro)


async def iterar(base_mongodb: str, coleccion: str, filtro: dict):
    async for documento in mongo.base(base_mongodb)[coleccion].find(filtro).sort("ts", -1):
        yield documento


async def leer_configuracion(base_mongodb: str, clave: str) -> dict | None:
    return await mongo.base(base_mongodb)["configuracion"].find_one({"_id": clave})


async def guardar_configuracion(base_mongodb: str, clave: str, valores: dict) -> None:
    await mongo.base(base_mongodb)["configuracion"].update_one(
        {"_id": clave}, {"$set": valores}, upsert=True
    )
