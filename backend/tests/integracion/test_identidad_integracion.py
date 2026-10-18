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


async def test_ciclo_de_vida_de_un_usuario(prefijo_aislado):
    from app.datos import sqlserver

    tenant = await servicios.aprovisionar("acme", "ACME", "admin@acme.com")
    base, tenant_id = tenant.base_sqlserver, str(tenant.id)

    usuario_id = repositorios.crear(base, tenant_id, "dba@acme.com", "Dba", "DBA", ["PROD", "DEV"])
    assert repositorios.obtener(base, tenant_id, usuario_id).ambientes == ("DEV", "PROD")

    repositorios.actualizar(base, tenant_id, usuario_id, "Dba", "CONSULTA", ["TESTING"])
    usuario = repositorios.obtener(base, tenant_id, usuario_id)
    assert (usuario.rol, usuario.ambientes) == ("CONSULTA", ("TESTING",))
    assert repositorios.contar_administradores_vigentes(base, tenant_id) == 1

    repositorios.dar_baja(base, tenant_id, usuario_id)
    assert [u.email for u in repositorios.listar(base, tenant_id, bajas=True)] == ["dba@acme.com"]
    assert [u.email for u in repositorios.listar(base, tenant_id, bajas=False)] == [
        "admin@acme.com"
    ]
    assert repositorios.purgar_bajas(base, tenant_id) == []

    repositorios.rehabilitar(base, tenant_id, usuario_id)
    assert repositorios.obtener(base, tenant_id, usuario_id).estado == "invitado"

    repositorios.dar_baja(base, tenant_id, usuario_id)
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            "UPDATE dbo.usuario SET fecha_baja = DATEADD(DAY, -31, SYSUTCDATETIME()) WHERE id = ?",
            usuario_id,
        )
        conexion.commit()
    assert repositorios.purgar_bajas(base, tenant_id) == ["dba@acme.com"]
    assert repositorios.obtener(base, tenant_id, usuario_id) is None

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
