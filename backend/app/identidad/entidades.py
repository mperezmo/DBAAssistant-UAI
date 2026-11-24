"""Entidades de identidad: usuarios, sesiones y permisos por rol."""

from dataclasses import asdict, dataclass, field
from datetime import datetime

# Roles de una empresa (TFI, 10.4.2.3). Consulta pasó a llamarse Lectura (migración 016).
ROLES = ("ADMINISTRADOR", "DBA", "ANALISTA", "LECTURA")
ROL_ANALISTA = "ANALISTA"
ROL_LECTURA = "LECTURA"
# Rol de la plataforma (no de una empresa): entra a una empresa solo en sesiones de soporte.
ROL_OPERADOR = "OPERADOR"
MODULO_SOPORTE_OPERADOR = "soporte_operador"
AMBIENTES = ("DEV", "TESTING", "PROD")
AMBIENTES_SIN_PROD = ("DEV", "TESTING")
# Roles con las facultades operativas del DBA. El Administrador también las tiene, para
# que un equipo sin DBA designado pueda operar (ver docs/arquitectura.md).
ROLES_OPERATIVOS = ("ADMINISTRADOR", "DBA")
DIAS_GRACIA_BAJA = 30


# Acciones que el Administrador puede restringir por usuario y ambiente (CU-001-002-C). El
# acceso a cada ambiente son los ambientes habilitados del usuario (usuario_ambiente).
MODIFICAR = "modificar"
PROMOVER = "promover"
EJECUTAR_REMEDIACIONES = "ejecutar_remediaciones"
AUTORIZAR_REMEDIACIONES = "autorizar_remediaciones"
APLICAR_BUENAS_PRACTICAS = "aplicar_buenas_practicas"
CONFIGURAR_ALERTAS = "configurar_alertas"
ATENDER_ALERTAS = "atender_alertas"
ACCIONES = (
    MODIFICAR,
    PROMOVER,
    EJECUTAR_REMEDIACIONES,
    AUTORIZAR_REMEDIACIONES,
    APLICAR_BUENAS_PRACTICAS,
    CONFIGURAR_ALERTAS,
    ATENDER_ALERTAS,
)
ACCIONES_ANALISTA_SIN_PROD = frozenset({MODIFICAR, PROMOVER})


def techo(rol: str, ambiente: str | None) -> frozenset[str]:
    """Lo máximo que el rol permite en el ambiente: el Administrador puede restringirlo por
    usuario, nunca ampliarlo.

    Administrador y DBA, todas las acciones en todos los ambientes; el Analista modifica y
    promueve solo en DEV y TESTING (en PROD solo lee); Lectura, ninguna.
    """
    if rol in ROLES_OPERATIVOS and (ambiente in AMBIENTES):
        return frozenset(ACCIONES)
    if rol == ROL_ANALISTA and ambiente in AMBIENTES_SIN_PROD:
        return ACCIONES_ANALISTA_SIN_PROD
    return frozenset()


def puede_modificar_en(rol: str, ambiente: str | None) -> bool:
    """Si el rol, por sí solo, modifica datos o estructura en el ambiente."""
    return MODIFICAR in techo(rol, ambiente)


def clave_restriccion(ambiente: str, accion: str) -> str:
    return f"{ambiente}:{accion}"


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
    ROL_ANALISTA: frozenset(
        {
            "inicio",
            "chat",
            "monitor",
            "alertas",
            "sandbox",
            "contexto",
            "schema",
            "buenas_practicas",
        }
    ),
    ROL_LECTURA: frozenset({"inicio", "chat", "monitor", "alertas", "contexto", "schema"}),
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
    # Acciones que el Administrador le quitó, como "AMBIENTE:accion" (CU-001-002-C).
    restricciones: tuple[str, ...] = field(default_factory=tuple)

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
    # Se releen del usuario en cada petición (identidad.dependencias.sesion_actual), igual
    # que el rol: los cambios del Administrador rigen desde la acción siguiente.
    ambientes: tuple[str, ...] = AMBIENTES
    restricciones: tuple[str, ...] = ()

    def puede_ver(self, modulo: str) -> bool:
        return modulo in MODULOS_POR_ROL.get(self.rol, frozenset())

    def puede(self, accion: str, ambiente: str | None) -> bool:
        """Permiso efectivo: acceso al ambiente, techo del rol y sin restricción."""
        return (
            ambiente in self.ambientes
            and accion in techo(self.rol, ambiente)
            and clave_restriccion(ambiente, accion) not in self.restricciones
        )

    def puede_en_todos(self, accion: str) -> bool:
        """Para lo que alcanza a todas las conexiones (por ejemplo, una regla general)."""
        return all(self.puede(accion, a) for a in AMBIENTES)

    def puede_modificar(self, ambiente: str | None) -> bool:
        return self.puede(MODIFICAR, ambiente)

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
