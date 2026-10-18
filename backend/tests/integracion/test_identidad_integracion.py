import pytest
from pymongo import MongoClient

from app.core.secretos import proveedor_secretos
from app.identidad import repositorios
from app.tenants import servicios

pytestmark = pytest.mark.integracion


async def test_ingreso_vincula_al_invitado_y_luego_lo_encuentra_por_su_identidad(
    prefijo_aislado,
):
    tenant = await servicios.aprovisionar("acme", "ACME", "admin@acme.com")
    base, tenant_id = tenant.base_sqlserver, str(tenant.id)

    invitado = repositorios.buscar_para_ingreso(base, tenant_id, "google|123", "admin@acme.com")
    assert invitado.estado == "invitado" and invitado.rol == "ADMINISTRADOR"

    repositorios.activar(base, invitado.id, "google|123", "Admin ACME")

    activo = repositorios.buscar_para_ingreso(base, tenant_id, "google|123", "otro@mail.com")
    assert (activo.estado, activo.nombre, activo.auth0_usuario_id) == (
        "activo",
        "Admin ACME",
        "google|123",
    )
    # Ya vinculado, el email solo no alcanza para encontrarlo con otra identidad.
    assert repositorios.buscar_para_ingreso(base, tenant_id, "auth0|999", "admin@acme.com") is None

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
