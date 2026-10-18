import pytest

from app.core.config import Configuracion
from app.core.secretos import ProveedorEntorno, SecretoNoEncontradoError
from app.datos import sqlserver


def test_cadena_conexion_usa_driver_odbc_y_cifrado():
    cfg = Configuracion(sqlserver_host="db", sqlserver_puerto=1433, sqlserver_base="app")

    cadena = sqlserver.cadena_conexion(cfg, "cl;ave")

    assert "DRIVER={ODBC Driver 18 for SQL Server}" in cadena
    assert "SERVER=db,1433" in cadena
    assert "DATABASE=app" in cadena
    assert "PWD={cl;ave}" in cadena  # las llaves protegen caracteres especiales
    assert "Encrypt=yes" in cadena


def test_proveedor_entorno_lee_variable_con_prefijo(monkeypatch):
    monkeypatch.setenv("SECRETO_MONGODB_URI", "mongodb://local")

    assert ProveedorEntorno().obtener("mongodb_uri") == "mongodb://local"


def test_proveedor_entorno_falla_si_no_existe(monkeypatch):
    monkeypatch.delenv("SECRETO_INEXISTENTE", raising=False)

    with pytest.raises(SecretoNoEncontradoError):
        ProveedorEntorno().obtener("inexistente")
