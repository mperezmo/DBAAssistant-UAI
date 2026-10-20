"""Entidades de la bitácora de auditoría."""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core import contexto


@dataclass(frozen=True)
class EventoAuditoria:
    """Evento de seguridad o configuración (sesiones, usuarios, permisos, cambios)."""

    accion: str  # formato recurso.accion, por ejemplo "usuario.creado"
    usuario: str
    tenant_id: str
    detalle: dict[str, Any] = field(default_factory=dict)
    # Por defecto, la IP de la petición en curso (None en procesos en segundo plano).
    ip: str | None = field(default_factory=contexto.ip.get)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    def a_documento(self) -> dict[str, Any]:
        return asdict(self)
