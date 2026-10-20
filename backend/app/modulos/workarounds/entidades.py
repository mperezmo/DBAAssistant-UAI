"""Entidades de la biblioteca de workarounds (CU-000-005)."""

from dataclasses import dataclass, field
from datetime import datetime

CATEGORIAS = ("performance", "espacio", "mantenimiento", "servicio")
CRITICIDADES = ("info", "warning", "critical")
ESTADOS_EJECUCION = ("en_curso", "exitosa", "fallida", "revertida")
ORIGENES = ("web", "mobile", "automatica")
# Eventos de alerta que admiten ejecución automática de un integrado apto.
EVENTOS_AUTOMATIZABLES = ("log", "instancia", "base")
# Tiempo para que otro usuario autorice un pedido y, una vez autorizado, para ejecutarlo.
VIGENCIA_PEDIDO_HORAS = 24
VIGENCIA_AUTORIZACION_HORAS = 2


@dataclass(frozen=True)
class Paso:
    """Un paso de un workaround: un lote T-SQL o un comando PowerShell por WinRM."""

    descripcion: str
    sql: str | None = None
    powershell: str | None = None


@dataclass(frozen=True)
class Propio:
    """Workaround creado por un DBA. Los integrados se definen en ``integrados.py``."""

    id: str
    tenant_id: str
    nombre: str
    descripcion: str
    categoria: str
    criticidad: str
    ambientes: tuple[str, ...]
    roles: tuple[str, ...]
    transaccional: bool
    condicion: str | None
    version_actual: int
    version_aprobada: int | None
    pasos: tuple[str, ...]
    creado_por: str = ""
    creado_en: datetime | None = None


@dataclass(frozen=True)
class Version:
    version: int
    pasos: tuple[str, ...]
    creada_por: str
    creada_en: datetime
    validada_en: datetime | None = None


@dataclass
class Ejecucion:
    id: int
    workaround: str
    workaround_nombre: str
    version: int | None
    conexion_id: str
    conexion_alias: str
    ambiente: str
    base: str | None
    parametros: dict
    alerta_id: int | None
    origen: str
    estado: str
    pasos: list[dict] = field(default_factory=list)
    error: str | None = None
    ejecutada_por: str = ""
    autorizada_por: str | None = None
    iniciada_en: datetime | None = None
    finalizada_en: datetime | None = None


@dataclass(frozen=True)
class Autorizacion:
    id: str
    workaround: str
    workaround_nombre: str
    conexion_id: str
    conexion_alias: str
    motivo: str
    solicitada_por: str
    solicitada_en: datetime
    estado: str
    resuelta_por: str | None
    resuelta_en: datetime | None
    vence_en: datetime


@dataclass(frozen=True)
class Automatizacion:
    id: str
    evento: str
    workaround: str
    conexion_id: str | None
    activa: bool
    creada_por: str
