"""Entidades de conexiones a instancias SQL Server."""

from dataclasses import dataclass
from datetime import time

ZONA_HORARIA_POR_DEFECTO = "America/Argentina/Buenos_Aires"


@dataclass(frozen=True)
class Conexion:
    """Conexión registrada. Las contraseñas solo existen cifradas y nunca salen de la API."""

    id: str
    tenant_id: str
    alias: str
    host: str
    puerto: int | None
    instancia: str | None
    ambiente: str
    usuario_sql: str
    password_sql_cifrada: str
    winrm_usuario: str | None = None
    winrm_password_cifrada: str | None = None
    winrm_puerto: int | None = None
    winrm_https: bool = False
    # Ventana de mantenimiento en la hora local de la instancia; puede cruzar la medianoche.
    ventana_inicio: time | None = None
    ventana_fin: time | None = None
    zona_horaria: str = ZONA_HORARIA_POR_DEFECTO

    @property
    def tiene_winrm(self) -> bool:
        return bool(self.winrm_usuario and self.winrm_password_cifrada)


@dataclass(frozen=True)
class DatosConexion:
    """Datos que carga el usuario. Una contraseña vacía en una edición conserva la actual."""

    alias: str
    host: str
    puerto: int | None
    instancia: str | None
    ambiente: str
    usuario_sql: str
    password_sql: str | None
    winrm_usuario: str | None = None
    winrm_password: str | None = None
    winrm_puerto: int | None = None
    winrm_https: bool = False
    ventana_inicio: time | None = None
    ventana_fin: time | None = None
    zona_horaria: str = ZONA_HORARIA_POR_DEFECTO


@dataclass(frozen=True)
class ResultadoPrueba:
    version: str
    edicion: str
    bases: int


@dataclass(frozen=True)
class ServicioWindows:
    nombre: str
    nombre_visible: str
    estado: str
