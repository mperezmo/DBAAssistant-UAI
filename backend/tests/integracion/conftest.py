"""Pruebas de integración contra SQL Server y MongoDB reales.

Se ejecutan con ``pytest -m integracion``; requieren las variables de conexión
(por ejemplo SQLSERVER_HOST, SQLSERVER_PUERTO, SECRETO_SQLSERVER_PASSWORD y
SECRETO_MONGODB_URI) apuntando a motores descartables, porque crean y borran bases.
"""

import uuid

import pytest

from app.core.config import obtener_configuracion
from app.datos import mongo, sqlserver


@pytest.fixture
def prefijo_aislado(monkeypatch):
    """Usa prefijos únicos para no chocar con otras ejecuciones y limpia al final."""
    sufijo = uuid.uuid4().hex[:8]
    monkeypatch.setenv("SQLSERVER_PREFIJO_BASE", f"Prueba{sufijo}")
    monkeypatch.setenv("MONGODB_PREFIJO_BASE", f"prueba{sufijo}")
    obtener_configuracion.cache_clear()
    mongo.cliente.cache_clear()
    cfg = obtener_configuracion()
    yield cfg

    with sqlserver.conectar("master", autocommit=True) as conexion:
        cursor = conexion.cursor()
        bases = [
            fila[0]
            for fila in cursor.execute(
                "SELECT name FROM sys.databases WHERE name LIKE ?", f"Prueba{sufijo}%"
            ).fetchall()
        ]
        for base in bases:
            cursor.execute(f"ALTER DATABASE [{base}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
            cursor.execute(f"DROP DATABASE [{base}]")
    obtener_configuracion.cache_clear()
    mongo.cliente.cache_clear()
