"""Resolución del tenant a partir del subdominio de la petición."""

import time

from starlette.concurrency import run_in_threadpool

from app.tenants import repositorios
from app.tenants.entidades import (
    PATRON_SLUG,
    SUBDOMINIO_PLATAFORMA,
    SUBDOMINIO_REGISTRO,
    SUBDOMINIOS_RESERVADOS,
    Tenant,
)

# Caché en memoria para no consultar el catálogo en cada petición.
VIGENCIA_CACHE_SEGUNDOS = 60
_cache: dict[str, tuple[float, Tenant | None]] = {}


def slug_desde_host(host: str, dominio_base: str) -> str | None:
    """Devuelve el slug si el host es ``<slug>.<dominio_base>``; si no, None."""
    nombre = host.split(":", 1)[0].lower().rstrip(".")
    sufijo = f".{dominio_base.lower()}"
    if not nombre.endswith(sufijo):
        return None
    slug = nombre[: -len(sufijo)]
    return slug if PATRON_SLUG.match(slug) else None


def es_reservado(slug: str | None) -> bool:
    return slug in SUBDOMINIOS_RESERVADOS


def es_plataforma(host: str, dominio_base: str) -> bool:
    """Consola de los operadores: ``plataforma.<dominio_base>``."""
    return slug_desde_host(host, dominio_base) == SUBDOMINIO_PLATAFORMA


def es_registro(host: str, dominio_base: str) -> bool:
    """Registro de empresas invitadas: ``registrar-empresa.<dominio_base>``."""
    return slug_desde_host(host, dominio_base) == SUBDOMINIO_REGISTRO


async def obtener_tenant(slug: str) -> Tenant | None:
    ahora = time.monotonic()
    guardado = _cache.get(slug)
    if guardado and ahora - guardado[0] < VIGENCIA_CACHE_SEGUNDOS:
        return guardado[1]
    tenant = await run_in_threadpool(repositorios.obtener_por_slug, slug)
    _cache[slug] = (ahora, tenant)
    return tenant


def limpiar_cache() -> None:
    _cache.clear()
