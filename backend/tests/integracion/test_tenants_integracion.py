import pytest
from pymongo import MongoClient

from app.core.errores import ErrorDominio
from app.core.secretos import proveedor_secretos
from app.datos import migraciones, sqlserver
from app.tenants import repositorios, servicios

pytestmark = pytest.mark.integracion


def _mongo_sincronico() -> MongoClient:
    return MongoClient(proveedor_secretos().obtener("mongodb_uri"))


async def test_crear_tenant_aprovisiona_todo(prefijo_aislado):
    tenant = await servicios.aprovisionar("acme", "ACME S.A.", "admin@acme.com")

    assert repositorios.obtener_por_slug("acme").estado == "activo"

    with sqlserver.conectar(tenant.base_sqlserver) as conexion:
        cursor = conexion.cursor()
        usuario = cursor.execute("SELECT email, rol, estado, tenant_id FROM dbo.usuario").fetchone()
        ambientes = cursor.execute("SELECT COUNT(*) FROM dbo.usuario_ambiente").fetchone()[0]
    assert (usuario.email, usuario.rol, usuario.estado) == (
        "admin@acme.com",
        "ADMINISTRADOR",
        "invitado",
    )
    assert str(usuario.tenant_id).lower() == str(tenant.id).lower()
    assert ambientes == 3

    cliente = _mongo_sincronico()
    try:
        db = cliente[tenant.base_mongodb]
        colecciones = set(db.list_collection_names())
        assert {"bitacora_auditoria", "bitacora_transacciones", "metricas"} <= colecciones
        assert db["bitacora_auditoria"].find_one({"accion": "tenant.aprovisionado"})
        indices = db["bitacora_auditoria"].index_information()
        assert indices["ttl_ts"]["expireAfterSeconds"] == 5 * 365 * 24 * 60 * 60
    finally:
        cliente.drop_database(tenant.base_mongodb)
        cliente.close()


async def test_tenants_quedan_aislados_en_bases_distintas(prefijo_aislado):
    uno = await servicios.aprovisionar("uno", "Uno", "a@uno.com")
    dos = await servicios.aprovisionar("dos", "Dos", "b@dos.com")

    assert uno.base_sqlserver != dos.base_sqlserver
    with sqlserver.conectar(dos.base_sqlserver) as conexion:
        emails = [f.email for f in conexion.cursor().execute("SELECT email FROM dbo.usuario")]
    assert emails == ["b@dos.com"]

    cliente = _mongo_sincronico()
    cliente.drop_database(uno.base_mongodb)
    cliente.drop_database(dos.base_mongodb)
    cliente.close()


async def test_slug_repetido_se_rechaza(prefijo_aislado):
    tenant = await servicios.aprovisionar("acme", "ACME", "admin@acme.com")

    with pytest.raises(ErrorDominio) as error:
        await servicios.aprovisionar("acme", "Otra", "otro@acme.com")
    assert error.value.codigo == "TEN_VAL_001"

    cliente = _mongo_sincronico()
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()


async def test_migrar_es_idempotente(prefijo_aislado):
    tenant = await servicios.aprovisionar("acme", "ACME", "admin@acme.com")

    servicios.migrar_todo()
    assert migraciones.aplicar(tenant.base_sqlserver, "tenant") == []

    cliente = _mongo_sincronico()
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
