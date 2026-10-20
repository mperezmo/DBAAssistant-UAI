"""Entidades de identidad: usuarios, sesiones y permisos por rol."""

from dataclasses import asdict, dataclass, field
from datetime import datetime

ROLES = ("ADMINISTRADOR", "DBA", "CONSULTA")
AMBIENTES = ("DEV", "TESTING", "PROD")
DIAS_GRACIA_BAJA = 30

# Módulos del menú habilitados por rol (docs/arquitectura.md, "Módulos por rol").
MODULOS_POR_ROL: dict[str, frozenset[str]] = {
    "ADMINISTRADOR": frozenset(
        {
            "inicio",
            "chat",
            "monitor",
            "alertas",
            "workarounds",
            "sandbox",
            "contexto",
            "schema",
            "auditoria",
            "buenas_practicas",
            "admin",
            "conexiones",
        }
    ),
    "DBA": frozenset(
        {
            "inicio",
            "chat",
            "monitor",
            "alertas",
            "workarounds",
            "sandbox",
            "contexto",
            "schema",
            "auditoria",
            "buenas_practicas",
            "conexiones",
        }
    ),
    "CONSULTA": frozenset({"inicio", "chat", "monitor", "alertas", "contexto", "schema"}),
}


@dataclass(frozen=True)
class Usuario:
    id: str
    tenant_id: str
    email: str
    nombre: str | None
    rol: str
    estado: str
    auth0_usuario_id: str | None
    ambientes: tuple[str, ...] = field(default_factory=tuple)
    fecha_baja: datetime | None = None
    ultimo_ingreso: datetime | None = None

    @property
    def puede_ingresar(self) -> bool:
        return self.estado in ("invitado", "activo")


@dataclass(frozen=True)
class Sesion:
    id: str
    usuario_id: str
    tenant_id: str
    tenant_slug: str
    email: str
    nombre: str
    rol: str
    creada_en: float
    # Momento de la última confirmación con segundo factor, que habilita acciones en PROD.
    segundo_factor_en: float | None = None

    def puede_ver(self, modulo: str) -> bool:
        return modulo in MODULOS_POR_ROL.get(self.rol, frozenset())

    def a_dict(self) -> dict:
        return asdict(self)

    @property
    def iniciales(self) -> str:
        partes = (self.nombre or self.email).replace("@", " ").replace(".", " ").split()
        return "".join(p[0] for p in partes[:2]).upper()
