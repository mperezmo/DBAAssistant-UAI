"""Entidades del dominio de tenants."""

import re
from dataclasses import dataclass
from uuid import UUID

from app.core.config import Configuracion
from app.core.errores import ErrorDominio

# Minúsculas, números y guiones; se usa para nombrar las bases del tenant.
PATRON_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")


def validar_slug(slug: str) -> str:
    if not PATRON_SLUG.match(slug):
        raise ErrorDominio(
            "TEN_VAL_002", "Usar 1 a 40 caracteres: minúsculas, números y guiones intermedios."
        )
    return slug


def nombres_de_bases(slug: str, cfg: Configuracion) -> tuple[str, str]:
    """Devuelve (base SQL Server, base MongoDB) del tenant."""
    sufijo = validar_slug(slug).replace("-", "_")
    return f"{cfg.sqlserver_prefijo_base}_T_{sufijo}", f"{cfg.mongodb_prefijo_base}_t_{sufijo}"


@dataclass(frozen=True)
class Tenant:
    id: UUID
    slug: str
    nombre: str
    base_sqlserver: str
    base_mongodb: str
    estado: str
    auth0_organizacion_id: str | None = None
