"""Entidades de la gestión de alertas (CU-000-004)."""

from dataclasses import dataclass
from datetime import datetime

from app.web.i18n import formatear

SEVERIDADES = ("info", "warning", "critical")
OPERADORES = (">", ">=", "<", "<=")
ACCIONES_ATENCION = ("manual", "falsa_alarma")
SOSTENIDA_POR_DEFECTO_SEGUNDOS = 60


def rango_severidad(severidad: str) -> int:
    return SEVERIDADES.index(severidad)


@dataclass(frozen=True)
class Regla:
    """Regla propia sobre una métrica (CU-000-004-A)."""

    id: str
    tenant_id: str
    nombre: str
    metrica: str
    operador: str
    valor: float
    severidad: str
    conexion_id: str | None
    sostenida_segundos: int
    destinatarios: tuple[str, ...]
    push: bool
    activa: bool = True
    creada_por: str = ""
    email: bool = False

    def cumple(self, valor: float | None) -> bool:
        if valor is None:
            return False
        return {
            ">": valor > self.valor,
            ">=": valor >= self.valor,
            "<": valor < self.valor,
            "<=": valor <= self.valor,
        }[self.operador]


@dataclass(frozen=True)
class Condicion:
    """Situación evaluada en una lectura que puede disparar o actualizar una alerta."""

    clave: str
    origen: str
    severidad: str
    titulo: str
    descripcion: str
    sostenida_segundos: int
    destinatarios: tuple[str, ...]
    push: bool
    email: bool = False
    metrica: str | None = None
    valor: float | None = None
    umbral: float | None = None
    unidad: str | None = None
    regla_id: str | None = None
    # Título y descripción como (clave de traducción, parámetros); los de una regla propia
    # son el nombre que le puso el usuario y no se traducen.
    textos: dict | None = None


def texto_visible(guardado: str, textos: dict | None, campo: str) -> str:
    """Texto de una alerta en el idioma de quien la ve.

    Las alertas guardan su título y descripción como clave de traducción y parámetros;
    las anteriores a ese cambio y las de reglas propias muestran el texto guardado.
    """
    referencia = (textos or {}).get(campo)
    if not referencia:
        return guardado
    clave, parametros = referencia
    return formatear(clave, **parametros)


@dataclass(frozen=True)
class Alerta:
    id: int
    tenant_id: str
    conexion_id: str
    conexion_alias: str
    servidor: str
    ambiente: str
    origen: str
    clave: str
    regla_id: str | None
    titulo: str
    descripcion: str
    severidad: str
    metrica: str | None
    valor: float | None
    umbral: float | None
    unidad: str | None
    estado: str
    normalizada: bool
    ocurrencias: int
    disparada_en: datetime
    actualizada_en: datetime
    normalizada_en: datetime | None = None
    atendida_en: datetime | None = None
    atendida_por: str | None = None
    accion: str | None = None
    comentario: str | None = None
    textos: dict | None = None

    @property
    def titulo_visible(self) -> str:
        return texto_visible(self.titulo, self.textos, "titulo")

    @property
    def descripcion_visible(self) -> str:
        return texto_visible(self.descripcion, self.textos, "descripcion")


@dataclass(frozen=True)
class Desestimada:
    """Condición marcada como esperada en una instancia: no genera alertas."""

    id: str
    conexion_id: str
    clave: str
    titulo: str
    conexion_alias: str
    motivo: str
    hasta: datetime | None
    creada_por: str
    creada_en: datetime
    textos: dict | None = None

    @property
    def titulo_visible(self) -> str:
        return texto_visible(self.titulo, self.textos, "titulo")


@dataclass(frozen=True)
class ResultadoDisparo:
    alerta: Alerta
    nueva: bool
    # La alerta ya existía y subió de severidad (por ejemplo, de warning a critical).
    escalo: bool
