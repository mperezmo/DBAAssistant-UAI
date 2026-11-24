"""Cliente MongoDB (driver oficial PyMongo, API asíncrona).

Cada tenant tiene su propia base; ``preparar_base_tenant`` crea sus colecciones
e índices de forma idempotente.
"""

from functools import lru_cache

from gridfs import AsyncGridFSBucket
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
# Archivos de los documentos de negocio (GridFS: colecciones documentos.files y .chunks).
BUCKET_DOCUMENTOS = "documentos"
COLECCION_HILOS = "chat_hilos"
COLECCION_SCHEMA = "schema_versiones"
# Versiones de la estructura de la base propia de la aplicación (Soporte).
COLECCION_SCHEMA_APLICACION = "schema_aplicacion"
COLECCION_INFORMES = "informes_buenas_practicas"
COLECCION_REGLAS_BP = "reglas_buenas_practicas"


@lru_cache
def cliente() -> AsyncMongoClient:
    """Cliente único por proceso; PyMongo administra el pool de conexiones."""
    return AsyncMongoClient(
        proveedor_secretos().obtener("mongodb_uri"), serverSelectionTimeoutMS=5000
    )


def base(nombre: str) -> AsyncDatabase:
    return cliente()[nombre]


def archivos(nombre: str) -> AsyncGridFSBucket:
    """Almacén de archivos (GridFS) de la base del tenant."""
    return AsyncGridFSBucket(base(nombre), bucket_name=BUCKET_DOCUMENTOS)


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
    await db[COLECCION_SCHEMA_APLICACION].create_index(
        [("tenant_id", ASCENDING), ("version", ASCENDING)],
        unique=True,
        name="version_por_tenant",
    )
    await db[COLECCION_INFORMES].create_index([("tenant_id", ASCENDING), ("ts", ASCENDING)])
    await db[COLECCION_HILOS].create_index(
        [("usuario_id", ASCENDING), ("actualizado_en", ASCENDING)]
    )

    await migrar_roles_contexto(db)

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


# Consulta pasó a llamarse Lectura y se sumó Analista, que tiene como mínimo lo de Lectura
# (migración 016 del tenant en SQL Server). En las tablas sensibles, donde estaba Consulta
# quedan Analista y Lectura. Idempotente: después de migrar ya no queda Consulta.
CAMPOS_CON_ROLES = ("roles_consulta", "roles_ven_columnas")


def _sin_consulta(campo: str) -> dict:
    return {
        "$concatArrays": [
            {"$filter": {"input": f"${campo}", "cond": {"$ne": ["$$this", "CONSULTA"]}}},
            ["ANALISTA", "LECTURA"],
        ]
    }


async def migrar_roles_contexto(db: AsyncDatabase) -> int:
    migrados = 0
    for campo in CAMPOS_CON_ROLES:
        resultado = await db[COLECCION_CONTEXTO].update_many(
            {campo: "CONSULTA"}, [{"$set": {campo: _sin_consulta(campo)}}]
        )
        migrados += resultado.modified_count
    return migrados


async def verificar() -> None:
    await cliente().admin.command("ping")
