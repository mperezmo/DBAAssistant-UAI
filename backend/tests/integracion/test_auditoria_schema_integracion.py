"""Auditoría, bitácora de excepciones e histórico de schema contra motores reales."""

import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from pymongo import MongoClient

from app.core import cifrado, contexto, excepciones
from app.core.secretos import proveedor_secretos
from app.datos import sqlserver
from app.identidad.entidades import Sesion
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria import servicios as auditoria_servicios
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.auditoria.servicios import Filtros
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import ZONA_HORARIA_POR_DEFECTO, DatosConexion
from app.modulos.historico_schema import servicios as historico
from app.tenants import servicios as tenants

pytestmark = pytest.mark.integracion

HOST = os.environ.get("SQLSERVER_HOST", "localhost")
PUERTO = int(os.environ.get("SQLSERVER_PUERTO", "14330"))


@pytest.fixture
def entorno(monkeypatch, redis_falso):
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    yield
    cifrado.reiniciar()


async def test_historico_de_schema(prefijo_aislado, entorno):
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    dba = Sesion("s", "u", str(tenant.id), "acme", "dba@acme.com", "Dba", "DBA", 0)
    password = proveedor_secretos().obtener("sqlserver_password")
    base = f"{prefijo_aislado.sqlserver_prefijo_base}_Ventas"
    sqlserver.crear_base_si_no_existe(base)
    servidor = f"{HOST},{PUERTO}"
    with sqlserver.conectar_externo(servidor, "sa", password, base) as c:
        c.cursor().execute("CREATE TABLE dbo.clientes (id INT PRIMARY KEY, nombre NVARCHAR(50))")
        c.cursor().execute("EXEC('CREATE VIEW dbo.v_clientes AS SELECT id FROM dbo.clientes')")
    conexion = await conexiones.crear(
        tenant, dba, DatosConexion("LOCAL", HOST, PUERTO, None, "DEV", "sa", password)
    )

    ddl = (
        "ALTER TABLE dbo.clientes ADD activo BIT NOT NULL DEFAULT 1\nGO\n"
        "CREATE INDEX IX_clientes_activo ON dbo.clientes (activo) INCLUDE (nombre)\nGO\n"
        "ALTER VIEW dbo.v_clientes AS SELECT id, nombre FROM dbo.clientes"
    )
    previa = await historico.preparar(conexion, base, ddl)
    assert previa is not None and previa.antes[base]["tabla:dbo.clientes"]
    with sqlserver.conectar_externo(servidor, "sa", password, base) as c:
        for lote in ddl.split("\nGO\n"):
            c.cursor().execute(lote)
    assert await historico.registrar(
        tenant, previa, ddl, "dev@acme.com", "promocion", "r1", "dba@acme.com"
    ) == [1]

    detalle = await historico.detalle(tenant, conexion.id, base, 1)
    cambios = {(c["operacion"], c["tipo"], c["nombre"]) for c in detalle["detalle"]}
    assert cambios == {
        ("modificado", "tabla", "dbo.clientes"),
        ("agregado", "indice", "dbo.clientes.IX_clientes_activo"),
        ("modificado", "vista", "dbo.v_clientes"),
    }
    tabla = next(c for c in detalle["detalle"] if c["tipo"] == "tabla")
    assert "[activo] bit NOT NULL DEFAULT ((1))" in tabla["despues"]
    assert "activo" not in tabla["antes"]

    # Cambio hecho fuera de la app: no genera versión, pero aparece al comparar con el actual.
    with sqlserver.conectar_externo(servidor, "sa", password, base) as c:
        c.cursor().execute("CREATE TABLE dbo.externa (a INT)")
    assert len((await historico.versiones(tenant, conexion.id, base))["versiones"]) == 1
    comparacion = await historico.comparar(tenant, conexion.id, base, 1, historico.ACTUAL)
    assert [(c["operacion"], c["nombre"]) for c in comparacion["cambios"]] == [
        ("agregado", "dbo.externa")
    ]

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()


async def test_bitacoras_y_exportacion(prefijo_aislado, entorno):
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    admin = Sesion("s", "u", str(tenant.id), "acme", "adm@acme.com", "Adm", "ADMINISTRADOR", 0)
    token = contexto.ip.set("192.168.10.42")
    try:
        for i, ambiente in enumerate(("PROD", "DEV", "PROD")):
            await auditoria.registrar_transaccion(
                tenant.base_mongodb,
                EventoAuditoria(
                    accion="chat.consulta_ejecutada",
                    usuario=f"user{i}@acme.com",
                    tenant_id=str(tenant.id),
                    detalle={
                        "tipo": "SELECT",
                        "ambiente": ambiente,
                        "sql": f"SELECT {i}",
                        "total": i,
                    },
                ),
            )
    finally:
        contexto.ip.reset(token)

    prod = await auditoria_servicios.listar(
        tenant, admin, "transacciones", Filtros(ambiente="PROD"), 1
    )
    assert prod["total"] == 2 and prod["registros"][0]["ip"] == "192.168.10.42"
    # Los filtros usan días de Buenos Aires: "hoy" es el de allá, no el UTC del servidor.
    hoy = datetime.now(ZoneInfo(ZONA_HORARIA_POR_DEFECTO)).date()
    texto = await auditoria_servicios.listar(
        tenant,
        admin,
        "transacciones",
        Filtros(desde=hoy, hasta=hoy, texto="SELECT 1", usuario="USER1"),
        1,
    )
    assert [r["usuario"] for r in texto["registros"]] == ["user1@acme.com"]
    lineas = await auditoria_servicios.exportar_csv(tenant, admin, "transacciones", Filtros())
    assert "".join([p async for p in lineas]).count("SELECT (chat)") == 3
    pdf = await auditoria_servicios.exportar_pdf(tenant, admin, "transacciones", Filtros())
    assert pdf.startswith(b"%PDF")
    sistema = await auditoria_servicios.listar(
        tenant, admin, "sistema", Filtros(tipo="auditoria"), 1
    )
    assert [r["accion"] for r in sistema["registros"]] == ["auditoria.exportado"] * 2

    # Bitácora de excepciones: el log de una petición del tenant llega a su base.
    await auditoria_servicios.cambiar_nivel_excepciones(tenant, admin, "WARNING")
    excepciones.iniciar(lambda: {tenant.slug: tenant.base_mongodb})
    try:
        token = contexto.tenant_slug.set(tenant.slug)
        logging.getLogger("app.prueba").error("CON_DB_003 ERP: sin conexión")
        logging.getLogger("app.prueba").info("solo informativo")
        contexto.tenant_slug.reset(token)
        for _ in range(50):
            registros = await auditoria_servicios.listar(tenant, admin, "excepciones", Filtros(), 1)
            if registros["total"]:
                break
            await asyncio.sleep(0.1)
    finally:
        excepciones.detener()
    [registro] = registros["registros"]
    assert registro["codigo"] == "CON_DB_003" and registro["nivel"] == "ERROR"

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
