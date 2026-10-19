import os

import pytest
from cryptography.fernet import Fernet
from pymongo import MongoClient

from app.core import cifrado
from app.core.errores import ErrorDominio
from app.core.secretos import proveedor_secretos
from app.identidad.entidades import Sesion
from app.modulos.conexiones import servicios
from app.modulos.conexiones.entidades import DatosConexion
from app.tenants import servicios as tenants

pytestmark = pytest.mark.integracion


@pytest.fixture
def clave(monkeypatch):
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    yield
    cifrado.reiniciar()


def _datos(password: str) -> DatosConexion:
    # El propio SQL Server de pruebas hace de instancia "del cliente".
    return DatosConexion(
        alias="LOCAL-DEV",
        host=os.environ.get("SQLSERVER_HOST", "localhost"),
        puerto=int(os.environ.get("SQLSERVER_PUERTO", "14330")),
        instancia=None,
        ambiente="DEV",
        usuario_sql="sa",
        password_sql=password,
    )


async def test_conexion_real_crear_probar_y_eliminar(prefijo_aislado, clave):
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    actor = Sesion("s", "u", str(tenant.id), "acme", "admin@acme.com", "Admin", "ADMINISTRADOR", 0)
    password = proveedor_secretos().obtener("sqlserver_password")

    resultado = await servicios.probar(tenant, _datos(password), None)
    assert resultado.version.startswith("16.")

    with pytest.raises(ErrorDominio) as error:
        await servicios.probar(tenant, _datos("incorrecta"), None)
    assert error.value.codigo == "CON_DB_000"

    conexion = await servicios.crear(tenant, actor, _datos(password))
    assert [c.alias for c in await servicios.listar(tenant)] == ["LOCAL-DEV"]
    # Probar la conexión guardada sin reescribir la contraseña.
    assert (await servicios.probar(tenant, _datos(None), conexion.id)).version == resultado.version

    await servicios.eliminar(tenant, actor, conexion.id)
    assert await servicios.listar(tenant) == []

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
