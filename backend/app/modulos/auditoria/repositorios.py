"""Persistencia de la bitácora de auditoría en la base MongoDB del tenant."""

from app.datos import mongo
from app.modulos.auditoria.entidades import EventoAuditoria


async def registrar(base_mongodb: str, evento: EventoAuditoria) -> None:
    await mongo.base(base_mongodb)["bitacora_auditoria"].insert_one(evento.a_documento())


async def registrar_transaccion(base_mongodb: str, evento: EventoAuditoria) -> None:
    """Operaciones ejecutadas sobre las instancias (workarounds, promociones a PROD)."""
    await mongo.base(base_mongodb)["bitacora_transacciones"].insert_one(evento.a_documento())
