"""Cliente Redis para la caché de queries de IA, contexto y métricas."""

from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import obtener_configuracion


@lru_cache
def cliente() -> Redis:
    return Redis.from_url(
        obtener_configuracion().redis_url, socket_timeout=5, decode_responses=True
    )


async def verificar() -> None:
    await cliente().ping()
