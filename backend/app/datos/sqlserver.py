"""Conexión a la base SQL Server propia de la aplicación vía ODBC (pyodbc).

pyodbc es sincrónico: desde código async se debe invocar con
``starlette.concurrency.run_in_threadpool`` para no bloquear el event loop.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pyodbc

from app.core.config import Configuracion, obtener_configuracion
from app.core.secretos import proveedor_secretos


def cadena_conexion(cfg: Configuracion, password: str) -> str:
    """Arma la cadena de conexión ODBC a partir de la configuración."""
    partes = {
        "DRIVER": f"{{{cfg.sqlserver_driver}}}",
        "SERVER": f"{cfg.sqlserver_host},{cfg.sqlserver_puerto}",
        "DATABASE": cfg.sqlserver_base,
        "UID": cfg.sqlserver_usuario,
        "PWD": f"{{{password}}}",
        "Encrypt": "yes",
        "TrustServerCertificate": "yes" if cfg.sqlserver_confiar_certificado else "no",
    }
    return ";".join(f"{clave}={valor}" for clave, valor in partes.items())


@contextmanager
def conectar() -> Iterator[pyodbc.Connection]:
    """Abre una conexión y garantiza su cierre al salir del bloque."""
    cfg = obtener_configuracion()
    password = proveedor_secretos().obtener("sqlserver_password")
    conexion = pyodbc.connect(
        cadena_conexion(cfg, password), timeout=cfg.sqlserver_timeout_segundos
    )
    try:
        yield conexion
    finally:
        conexion.close()


def verificar() -> None:
    """Ejecuta una consulta trivial; lanza excepción si la base no responde."""
    with conectar() as conexion:
        conexion.cursor().execute("SELECT 1").fetchone()
