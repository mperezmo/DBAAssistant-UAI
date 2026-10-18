"""Cliente MongoDB (driver oficial PyMongo, API asíncrona)."""

from functools import lru_cache

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import obtener_configuracion
from app.core.secretos import proveedor_secretos


@lru_cache
def cliente() -> AsyncMongoClient:
    """Cliente único por proceso; PyMongo administra el pool de conexiones."""
    return AsyncMongoClient(
        proveedor_secretos().obtener("mongodb_uri"), serverSelectionTimeoutMS=5000
    )


def base() -> AsyncDatabase:
    return cliente()[obtener_configuracion().mongodb_base]


async def verificar() -> None:
    await cliente().admin.command("ping")
