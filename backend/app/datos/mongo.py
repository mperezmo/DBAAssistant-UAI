"""Cliente MongoDB (driver oficial PyMongo, API asíncrona).

Cada tenant tiene su propia base; ``preparar_base_tenant`` crea sus colecciones
e índices de forma idempotente.
"""

from functools import lru_cache

from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import obtener_configuracion
from app.core.secretos import proveedor_secretos

# Requisito del TFI: las bitácoras se conservan 5 años.
RETENCION_BITACORAS_SEGUNDOS = 5 * 365 * 24 * 60 * 60

BITACORAS = (
    "bitacora_auditoria",
    "bitacora_transacciones",
    "bitacora_excepciones",
    "bitacora_sandbox",
)
COLECCION_METRICAS = "metricas"
COLECCION_CONTEXTO = "contexto_negocio"
COLECCION_HILOS = "chat_hilos"
COLECCION_SCHEMA = "schema_versiones"


@lru_cache
def cliente() -> AsyncMongoClient:
    """Cliente único por proceso; PyMongo administra el pool de conexiones."""
    return AsyncMongoClient(
        proveedor_secretos().obtener("mongodb_uri"), serverSelectionTimeoutMS=5000
    )


def base(nombre: str) -> AsyncDatabase:
    return cliente()[nombre]


async def preparar_base_tenant(nombre: str) -> None:
    """Crea las colecciones del tenant y sus índices si todavía no existen."""
    db = base(nombre)
    existentes = set(await db.list_collection_names())

    for coleccion in BITACORAS:
        if coleccion not in existentes:
            await db.create_collection(coleccion)
        await db[coleccion].create_index(
            [("ts", ASCENDING)], expireAfterSeconds=RETENCION_BITACORAS_SEGUNDOS, name="ttl_ts"
        )
        await db[coleccion].create_index([("usuario", ASCENDING), ("ts", ASCENDING)])

    # Una tabla sensible o un término por base: el índice único evita duplicados.
    await db[COLECCION_CONTEXTO].create_index(
        [
            ("tenant_id", ASCENDING),
            ("tipo", ASCENDING),
            ("conexion_id", ASCENDING),
            ("base", ASCENDING),
            ("clave", ASCENDING),
        ],
        unique=True,
        partialFilterExpression={"clave": {"$exists": True}},
        name="unico_por_base",
    )
    await db[COLECCION_SCHEMA].create_index(
        [
            ("tenant_id", ASCENDING),
            ("conexion_id", ASCENDING),
            ("base", ASCENDING),
            ("version", ASCENDING),
        ],
        unique=True,
        name="version_por_base",
    )
    await db[COLECCION_HILOS].create_index(
        [("usuario_id", ASCENDING), ("actualizado_en", ASCENDING)]
    )

    retencion = obtener_configuracion().monitor_retencion_dias * 24 * 60 * 60
    if COLECCION_METRICAS not in existentes:
        await db.create_collection(
            COLECCION_METRICAS,
            timeseries={"timeField": "ts", "metaField": "meta", "granularity": "seconds"},
            expireAfterSeconds=retencion,
        )
    else:
        # Aplica la retención vigente a colecciones creadas antes o con otro valor.
        await db.command("collMod", COLECCION_METRICAS, expireAfterSeconds=retencion)


async def verificar() -> None:
    await cliente().admin.command("ping")
