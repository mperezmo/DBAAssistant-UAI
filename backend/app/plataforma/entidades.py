"""Entidades de la plataforma: operadores e invitaciones para registrar empresas."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# El rol vive en identidad (lo usan los permisos por módulo); se reexporta para la plataforma.
from app.identidad.entidades import ROL_OPERADOR as ROL_OPERADOR

# La sesión de un operador no pertenece a ninguna empresa.
TENANT_PLATAFORMA = "plataforma"

# La invitación vence a las 24 h de enviarse o copiarse el link.
VIGENCIA_INVITACION = timedelta(hours=24)
# Código de verificación del registro.
VIGENCIA_CODIGO = timedelta(minutes=10)
INTENTOS_CODIGO = 5
DIGITOS_CODIGO = 6


def ahora_utc() -> datetime:
    """Hora UTC sin zona, como la guarda SQL Server (DATETIME2)."""
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class Operador:
    id: str
    email: str
    nombre: str | None
    estado: str
    auth0_usuario_id: str | None
    creado_por: str
    creado_en: datetime
    ultimo_ingreso: datetime | None = None
    idioma: str | None = None  # None: el del navegador

    @property
    def puede_ingresar(self) -> bool:
        return self.estado in ("invitado", "activo")


@dataclass(frozen=True)
class Invitacion:
    id: str
    email: str
    estado: str
    creada_por: str
    creada_en: datetime
    vence_en: datetime
    codigo_hash: str | None = None
    codigo_vence_en: datetime | None = None
    codigo_intentos: int = 0
    tenant_id: str | None = None
    usada_en: datetime | None = None
    revocada_en: datetime | None = None

    def vigente(self, ahora: datetime) -> bool:
        """Se puede empezar a registrar la empresa."""
        return self.estado == "pendiente" and self.vence_en > ahora

    def abierta(self, ahora: datetime) -> bool:
        """Se puede entrar al registro: vigente o con el alta ya iniciada (no vence)."""
        return self.vigente(ahora) or self.estado == "en_curso"

    def estado_visible(self, ahora: datetime) -> str:
        if self.estado == "pendiente" and self.vence_en <= ahora:
            return "vencida"
        return self.estado
