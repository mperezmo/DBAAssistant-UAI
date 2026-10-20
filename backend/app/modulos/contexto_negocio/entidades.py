"""Entidades del contexto de negocio (CU-000-006): tablas sensibles, glosario y reglas."""

from dataclasses import dataclass
from datetime import time

ACCIONES = ("SELECT", "INSERT", "UPDATE", "DELETE", "DDL")
EFECTOS = ("bloquear", "advertir")
PRIORIDADES = (1, 2, 3)  # alta, media, baja


@dataclass(frozen=True)
class TablaSensible:
    conexion_id: str
    base: str
    tabla: str  # esquema.tabla
    columnas: tuple[str, ...]  # se enmascaran para los roles que no las pueden ver
    roles_consulta: tuple[str, ...]  # quiénes pueden consultar la tabla
    roles_ven_columnas: tuple[str, ...]  # quiénes ven las columnas sin enmascarar
    doble_confirmacion: bool  # segundo factor para modificar la tabla
    descripcion: str = ""
    id: str = ""


@dataclass(frozen=True)
class Termino:
    conexion_id: str
    base: str
    termino: str
    entidad: str  # esquema.tabla o esquema.vista
    sinonimos: tuple[str, ...] = ()
    descripcion: str = ""  # definición en el modelo: filtros, joins, cálculos
    id: str = ""


@dataclass(frozen=True)
class Regla:
    descripcion: str
    acciones: tuple[str, ...]
    efecto: str  # bloquear o advertir
    prioridad: int = 2
    conexion_id: str | None = None  # None: todas las conexiones
    base: str | None = None  # None: todas las bases
    ambientes: tuple[str, ...] = ()  # vacío: todos
    tablas: tuple[str, ...] = ()  # vacío: todas
    # Franja horaria (hora local de la conexión) en la que rige; None: siempre.
    desde: time | None = None
    hasta: time | None = None
    activa: bool = True
    id: str = ""
