"""Conexión a SQL Server vía ODBC (pyodbc).

La aplicación usa una base central (catálogo de tenants) y una base por tenant;
``conectar`` recibe el nombre de la base a usar.

pyodbc es sincrónico: desde código async se debe invocar con
``starlette.concurrency.run_in_threadpool`` para no bloquear el event loop.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pyodbc

from app.core.config import Configuracion, obtener_configuracion
from app.core.secretos import proveedor_secretos

BASE_SISTEMA = "master"


def cadena_conexion(cfg: Configuracion, password: str, base: str) -> str:
    """Arma la cadena de conexión ODBC a partir de la configuración."""
    partes = {
        "DRIVER": f"{{{cfg.sqlserver_driver}}}",
        "SERVER": f"{cfg.sqlserver_host},{cfg.sqlserver_puerto}",
        "DATABASE": base,
        "UID": cfg.sqlserver_usuario,
        "PWD": f"{{{password}}}",
        "Encrypt": "yes",
        "TrustServerCertificate": "yes" if cfg.sqlserver_confiar_certificado else "no",
    }
    return ";".join(f"{clave}={valor}" for clave, valor in partes.items())


@contextmanager
def conectar(base: str, autocommit: bool = False) -> Iterator[pyodbc.Connection]:
    """Abre una conexión a ``base`` y garantiza su cierre al salir del bloque."""
    cfg = obtener_configuracion()
    password = proveedor_secretos().obtener("sqlserver_password")
    conexion = pyodbc.connect(
        cadena_conexion(cfg, password, base),
        timeout=cfg.sqlserver_timeout_segundos,
        autocommit=autocommit,
    )
    try:
        yield conexion
    finally:
        conexion.close()


def crear_base_si_no_existe(nombre: str) -> bool:
    """Crea la base ``nombre`` si no existe. Devuelve True si la creó.

    CREATE DATABASE no admite parámetros ni transacciones: el nombre debe venir
    validado por el llamador y se protege además con QUOTENAME.
    """
    with conectar(BASE_SISTEMA, autocommit=True) as conexion:
        cursor = conexion.cursor()
        if cursor.execute("SELECT DB_ID(?)", nombre).fetchone()[0] is not None:
            return False
        nombre_citado = cursor.execute("SELECT QUOTENAME(?)", nombre).fetchone()[0]
        cursor.execute(f"CREATE DATABASE {nombre_citado}")
        return True


def verificar() -> None:
    """Ejecuta una consulta trivial; lanza excepción si el motor no responde."""
    with conectar(BASE_SISTEMA) as conexion:
        conexion.cursor().execute("SELECT 1").fetchone()


def servidor_externo(host: str, puerto: int | None, instancia: str | None) -> str:
    """Arma el SERVER de ODBC: con puerto explícito se usa ``host,puerto``; con instancia
    con nombre y sin puerto, ``host\\instancia`` (requiere el servicio SQL Browser)."""
    if puerto:
        return f"{host},{puerto}"
    if instancia:
        return f"{host}\\{instancia}"
    return host


@contextmanager
def conectar_externo(
    servidor: str, usuario: str, password: str, base: str = BASE_SISTEMA, timeout: int = 5
) -> Iterator[pyodbc.Connection]:
    """Conexión a una instancia SQL Server de un cliente, con autenticación SQL.

    Se confía en el certificado del servidor porque las instancias de los clientes suelen
    usar certificados autofirmados (decisión documentada en docs/arquitectura.md).
    """
    cfg = obtener_configuracion()
    partes = {
        "DRIVER": f"{{{cfg.sqlserver_driver}}}",
        "SERVER": servidor,
        "DATABASE": base,
        "UID": usuario,
        "PWD": f"{{{password}}}",
        "Encrypt": "yes",
        "TrustServerCertificate": "yes",
    }
    # autocommit evita dejar transacciones implícitas abiertas en las conexiones que el
    # driver devuelve al pool; las operaciones de escritura las abrirán explícitamente.
    conexion = pyodbc.connect(
        ";".join(f"{clave}={valor}" for clave, valor in partes.items()),
        timeout=timeout,
        autocommit=True,
    )
    try:
        yield conexion
    finally:
        conexion.close()
