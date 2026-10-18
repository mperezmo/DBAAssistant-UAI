"""Persistencia de la bitácora de auditoría en la base MongoDB del tenant."""

from app.datos import mongo
from app.modulos.auditoria.entidades import EventoAuditoria


async def registrar(base_mongodb: str, evento: EventoAuditoria) -> None:
    await mongo.base(base_mongodb)["bitacora_auditoria"].insert_one(evento.a_documento())
