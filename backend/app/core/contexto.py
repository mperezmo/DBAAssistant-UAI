"""Contexto de la petición en curso: tenant, usuario, IP y ruta.

Se guarda en variables de contexto para que la bitácora de auditoría registre la IP y
la de excepciones sepa a qué tenant y usuario corresponde un log, sin pasar esos datos
por cada función. Las tareas asincrónicas creadas durante la petición heredan el valor.
"""

from contextvars import ContextVar

tenant_slug: ContextVar[str | None] = ContextVar("tenant_slug", default=None)
usuario: ContextVar[str | None] = ContextVar("usuario", default=None)
ip: ContextVar[str | None] = ContextVar("ip", default=None)
ruta: ContextVar[str | None] = ContextVar("ruta", default=None)
