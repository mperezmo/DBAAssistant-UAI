"""Operadores, invitaciones y alta por invitación contra el catálogo real (migración 002)."""

from datetime import timedelta

import pyodbc
import pytest
from pymongo import MongoClient

from app.core.secretos import proveedor_secretos
from app.datos import sqlserver
from app.plataforma import repositorios
from app.plataforma.entidades import ahora_utc
from app.tenants import repositorios as tenants
from app.tenants import servicios as tenants_servicios

pytestmark = pytest.mark.integracion


async def test_operadores_e_invitaciones_en_el_catalogo(prefijo_aislado):
    tenants_servicios.migrar_catalogo()

    assert repositorios.organizacion() is None
    repositorios.guardar_organizacion("org_plataforma")
    assert repositorios.organizacion() == "org_plataforma"

    operador = repositorios.crear_operador("ops@plataforma.com", "Ops", "sistema")
    assert operador.estado == "invitado"
    assert repositorios.crear_operador("ops@plataforma.com", None, "sistema") is None
    repositorios.activar_operador(operador.id, "google|ops", "Nombre de Google")
    encontrado = repositorios.buscar_para_ingreso("google|ops", "otro@mail.com")
    assert (encontrado.estado, encontrado.nombre) == ("activo", "Ops")
    repositorios.dar_de_baja_operador(operador.id)
    assert repositorios.listar_operadores() == []
    vuelto = repositorios.crear_operador("ops@plataforma.com", None, "otro")
    assert vuelto.id == operador.id and vuelto.estado == "invitado"
    assert vuelto.auth0_usuario_id is None

    ahora = ahora_utc()
    primera = repositorios.crear_invitacion("admin@pepe.com", "ops", ahora + timedelta(hours=24))
    segunda = repositorios.crear_invitacion("admin@pepe.com", "ops", ahora + timedelta(hours=24))
    assert repositorios.obtener_invitacion(primera.id).estado == "revocada"
    assert repositorios.obtener_invitacion_abierta("admin@pepe.com").id == segunda.id

    repositorios.guardar_codigo(segunda.id, "a" * 64, ahora + timedelta(minutes=10))
    for _ in range(5):
        repositorios.registrar_intento_fallido(segunda.id, 5)
    sin_codigo = repositorios.obtener_invitacion(segunda.id)
    assert sin_codigo.codigo_hash is None and sin_codigo.codigo_intentos == 5

    assert repositorios.iniciar_alta(segunda.id, ahora)
    assert not repositorios.revocar_invitacion(segunda.id)
    # Con una abierta (en curso), no puede existir otra abierta para el mismo email.
    with pytest.raises(pyodbc.IntegrityError):
        with sqlserver.conectar(prefijo_aislado.sqlserver_base_catalogo) as conexion:
            conexion.cursor().execute(
                """INSERT INTO dbo.invitacion_empresa (email, estado, creada_por, vence_en)
                   VALUES ('admin@pepe.com', 'pendiente', 'ops', ?)""",
                ahora + timedelta(hours=1),
            )
            conexion.commit()

    vencida = repositorios.crear_invitacion("otra@x.com", "ops", ahora - timedelta(seconds=5))
    assert not repositorios.iniciar_alta(vencida.id, ahora)


async def test_alta_por_invitacion_registra_al_administrador(prefijo_aislado):
    tenant = await tenants_servicios.aprovisionar(
        "pepe", "Pepe S.A.", "admin@pepe.com", "Pepe Argento"
    )
    try:
        resumen = tenants.listar_resumen()
        assert [(r["slug"], r["email_administrador"]) for r in resumen] == [
            ("pepe", "admin@pepe.com")
        ]
        with sqlserver.conectar(tenant.base_sqlserver) as conexion:
            nombre = conexion.cursor().execute("SELECT nombre FROM dbo.usuario").fetchone()[0]
        assert nombre == "Pepe Argento"
    finally:
        cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
        cliente.drop_database(tenant.base_mongodb)
        cliente.close()
