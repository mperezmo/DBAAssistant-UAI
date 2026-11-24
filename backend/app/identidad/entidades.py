"""Entidades de identidad: usuarios, sesiones y permisos por rol."""

from dataclasses import asdict, dataclass, field
from datetime import datetime

ROLES = ("ADMINISTRADOR", "DBA", "CONSULTA")
# Rol de la plataforma (no de una empresa): entra a una empresa solo en sesiones de soporte.
ROL_OPERADOR = "OPERADOR"
MODULO_SOPORTE_OPERADOR = "soporte_operador"
AMBIENTES = ("DEV", "TESTING", "PROD")
# Roles con las facultades operativas del DBA. El Administrador también las tiene, para
# que un equipo sin DBA designado pueda operar (ver docs/arquitectura.md).
ROLES_OPERATIVOS = ("ADMINISTRADOR", "DBA")
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
            "soporte",
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
    # Operador de la plataforma en una sesión de soporte: solo la vista acotada de Soporte
    # (salud de servicios y errores de la aplicación, sin datos de la empresa).
    ROL_OPERADOR: frozenset({MODULO_SOPORTE_OPERADOR}),
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
    idioma: str | None = None  # None: el del navegador

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

    @property
    def es_operador(self) -> bool:
        return self.rol == ROL_OPERADOR

    def en_menu(self, modulo: str) -> bool:
        """El Operador ve Soporte en el menú aunque su módulo sea la vista acotada."""
        return self.puede_ver(modulo) or (
            modulo == "soporte" and self.puede_ver(MODULO_SOPORTE_OPERADOR)
        )

    def a_dict(self) -> dict:
        return asdict(self)

    @property
    def iniciales(self) -> str:
        partes = (self.nombre or self.email).replace("@", " ").replace(".", " ").split()
        return "".join(p[0] for p in partes[:2]).upper()
