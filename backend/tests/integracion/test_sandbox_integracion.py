"""Módulo Sandbox del backend contra el servicio Sandbox real.

Requiere el servicio escuchando en SANDBOX_URL (con SECRETO_SANDBOX_TOKEN); si no está,
la prueba se omite. La CI prueba el servicio por separado (job "Sandbox de punta a punta").
"""

import asyncio
import os
import time
from dataclasses import replace

import httpx2
import pytest
from cryptography.fernet import Fernet
from pymongo import MongoClient

from app.core import cifrado
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.core.secretos import proveedor_secretos
from app.datos import sqlserver
from app.identidad.entidades import Sesion
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import DatosConexion
from app.modulos.sandbox import servicios
from app.modulos.workarounds import servicios as workarounds
from app.modulos.workarounds.entidades import Propio
from app.tenants import servicios as tenants

pytestmark = pytest.mark.integracion

HOST = os.environ.get("SQLSERVER_HOST", "localhost")
PUERTO = int(os.environ.get("SQLSERVER_PUERTO", "14330"))


def _servicio_disponible() -> bool:
    try:
        httpx2.get(obtener_configuracion().sandbox_url, timeout=2)
        return bool(os.environ.get("SECRETO_SANDBOX_TOKEN"))
    except httpx2.HTTPError:
        return False


@pytest.fixture
def entorno(monkeypatch):
    if not _servicio_disponible():
        pytest.skip("Servicio Sandbox no disponible")
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    avisos = []

    async def avisar(tenant, roles, titulo, cuerpo, ruta):
        avisos.append(cuerpo)

    monkeypatch.setattr(servicios.notificaciones, "avisar", avisar)
    yield avisos
    cifrado.reiniciar()


async def _esperar_sincronizacion(tenant, conexion_id):
    for _ in range(300):
        estado = await servicios.estado(tenant, conexion_id)
        if estado["estado"] != "sincronizando":
            return estado
        await asyncio.sleep(1)
    raise AssertionError("La sincronización no terminó")


async def test_sandbox_de_punta_a_punta(prefijo_aislado, entorno):
    avisos = entorno
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    dba = Sesion("s", "u", str(tenant.id), "acme", "dba@acme.com", "Dba", "DBA", 0)
    password = proveedor_secretos().obtener("sqlserver_password")
    base = f"{prefijo_aislado.sqlserver_prefijo_base}_Ventas"
    sqlserver.crear_base_si_no_existe(base)
    with sqlserver.conectar_externo(f"{HOST},{PUERTO}", "sa", password, base) as c:
        c.cursor().execute("CREATE TABLE dbo.factura (id INT PRIMARY KEY, estado INT)")
        c.cursor().execute("INSERT dbo.factura VALUES (1, 0), (2, 0)")
    # El servicio corre en el host: alcanza el SQL Server de pruebas por 127.0.0.1.
    conexion = await conexiones.crear(
        tenant, dba, DatosConexion("LOCAL", "127.0.0.1", PUERTO, None, "PROD", "sa", password)
    )

    assert (await servicios.estado(tenant, conexion.id))["estado"] == "sin_crear"
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(tenant, dba, conexion.id, base, "SELECT 1", False)
    assert error.value.codigo == "SBX_SNP_001"

    await servicios.sincronizar(tenant, dba, conexion.id)
    estado = await _esperar_sincronizacion(tenant, conexion.id)
    assert estado["estado"] == "listo" and estado["desactualizado"] is False
    assert any(b["base"] == base and b["estado"] == "ok" for b in estado["bases"])

    assert (
        await servicios.ejecutar(tenant, dba, conexion.id, base, "SELECT * FROM dbo.nada", True)
    )["estado"] in ("ok", "sintaxis")
    resultado = await servicios.ejecutar(
        tenant,
        dba,
        conexion.id,
        base,
        "UPDATE dbo.factura SET estado = 1\nGO\nSELECT * FROM dbo.factura",
        False,
    )
    assert resultado["estado"] == "ok" and resultado["avisos"] == []
    assert resultado["muestra"]["filas"] == []  # solo estructura

    [registro] = await servicios.historial(tenant, None, None, None)
    assert registro["id"] == resultado["id"] and registro["usuario"] == "dba@acme.com"

    # Promoción: comparación con la instancia real y segundo factor en PROD.
    previa = await servicios.previa(tenant, dba, resultado["id"])
    assert previa["real"]["filas_estimadas"] >= 2 and previa["ambiente"] == "PROD"
    with pytest.raises(ErrorDominio) as error:
        await servicios.promover(tenant, dba, resultado["id"])
    assert error.value.codigo == "AMB_VAL_000"
    promovida = await servicios.promover(
        tenant, replace(dba, segundo_factor_en=time.time()), resultado["id"]
    )
    assert promovida == {"estado": "exitosa", "filas": 2}
    with sqlserver.conectar_externo(f"{HOST},{PUERTO}", "sa", password, base) as c:
        assert c.cursor().execute("SELECT SUM(estado) FROM dbo.factura").fetchone()[0] == 2
    assert avisos and "2 filas" in avisos[-1]
    with pytest.raises(ErrorDominio) as error:
        await servicios.promover(
            tenant, replace(dba, segundo_factor_en=time.time()), resultado["id"]
        )
    assert error.value.codigo == "SBX_PRM_000"

    # Workaround propio: validarlo en el Sandbox lo aprueba para PROD; una versión que
    # falla deja vigente la aprobada anterior.
    def propio(paso: str) -> Propio:
        return Propio(
            "",
            "",
            "Cerrar facturas",
            "x",
            "mantenimiento",
            "info",
            ("PROD",),
            ("DBA",),
            True,
            None,
            0,
            None,
            (paso,),
        )

    creado = await workarounds.guardar_propio(
        tenant, dba, None, propio("UPDATE dbo.factura SET estado = 3"), (conexion.id, base)
    )
    assert creado["estado"] == "pendiente_sandbox"
    validado = await workarounds.validar_en_sandbox(tenant, dba, creado["id"], conexion.id, base)
    assert validado["estado"] == "aprobado" and validado["version_aprobada"] == 1
    await workarounds.guardar_propio(
        tenant,
        dba,
        creado["id"],
        propio("UPDATE dbo.factura SET estado = 1 / 0"),
        (conexion.id, base),
    )
    with pytest.raises(ErrorDominio) as error:
        await workarounds.validar_en_sandbox(tenant, dba, creado["id"], conexion.id, base)
    assert error.value.codigo == "WKR_SBX_000"
    detalle = await workarounds.detalle(tenant, creado["id"])
    assert detalle["version_actual"] == 2 and detalle["version_aprobada"] == 1

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    assert (
        cliente[tenant.base_mongodb]["bitacora_transacciones"].count_documents(
            {"accion": "sandbox.promovido"}
        )
        == 1
    )
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
