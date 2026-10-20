import asyncio
import contextlib
import os
import threading
import time
from dataclasses import replace
from datetime import time as hora

import pytest
from cryptography.fernet import Fernet
from pymongo import MongoClient

from app.core import cifrado
from app.core.errores import ErrorDominio
from app.core.secretos import proveedor_secretos
from app.datos import sqlserver
from app.identidad import repositorios as usuarios
from app.identidad.entidades import Sesion
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import DatosConexion
from app.modulos.workarounds import servicios
from app.modulos.workarounds.entidades import Propio
from app.tenants import servicios as tenants

pytestmark = pytest.mark.integracion

HOST = os.environ.get("SQLSERVER_HOST", "localhost")
PUERTO = int(os.environ.get("SQLSERVER_PUERTO", "14330"))
SERVIDOR = f"{HOST},{PUERTO}"


@pytest.fixture
def entorno(monkeypatch):
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    avisos = []

    async def avisar(tenant, roles, titulo, cuerpo, ruta):
        avisos.append((roles, titulo, cuerpo))

    monkeypatch.setattr(servicios.notificaciones, "avisar", avisar)
    yield avisos
    cifrado.reiniciar()


async def _esperar(tenant, ejecucion_id: int) -> dict:
    for _ in range(100):
        e = await servicios.obtener_ejecucion(tenant, ejecucion_id)
        if e["estado"] != "en_curso":
            # La bitácora y los avisos se registran al terminar la tarea.
            while servicios._tareas:
                await asyncio.sleep(0.05)
            return e
        await asyncio.sleep(0.1)
    raise AssertionError("La ejecución no terminó")


def _sesion(tenant, usuario_id, email, rol, segundo_factor=None) -> Sesion:
    return Sesion("s", usuario_id, str(tenant.id), "acme", email, email, rol, 0, segundo_factor)


async def test_workarounds_de_punta_a_punta(prefijo_aislado, entorno):
    avisos = entorno
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    base_sql, tenant_id = tenant.base_sqlserver, str(tenant.id)
    dba_id = usuarios.crear(base_sql, tenant_id, "dba@acme.com", None, "DBA", ("DEV", "PROD"))
    otro_id = usuarios.crear(base_sql, tenant_id, "dba2@acme.com", None, "DBA", ("DEV",))
    dba = _sesion(tenant, dba_id, "dba@acme.com", "DBA")
    otro = _sesion(tenant, otro_id, "dba2@acme.com", "DBA")
    password = proveedor_secretos().obtener("sqlserver_password")
    conexion = await conexiones.crear(
        tenant, dba, DatosConexion("LOCAL", HOST, PUERTO, None, "DEV", "sa", password)
    )

    # Base de pruebas con una tabla para provocar un bloqueo.
    base = f"{prefijo_aislado.sqlserver_prefijo_base}_Wk"
    sqlserver.crear_base_si_no_existe(base)
    with sqlserver.conectar_externo(SERVIDOR, "sa", password, base) as c:
        c.cursor().execute(
            "CREATE TABLE dbo.t (id INT PRIMARY KEY, x INT); INSERT dbo.t VALUES (1, 0)"
        )
    assert base in await servicios.bases(tenant, conexion.id)

    # Biblioteca: los integrados.
    assert {w["id"] for w in await servicios.biblioteca(tenant)} >= {"kill_bloqueos", "shrink_log"}

    # Checkpoint y shrink del log.
    for workaround in ("checkpoint", "shrink_log"):
        diag = await servicios.diagnostico(tenant, dba, workaround, conexion.id, base, {})
        assert diag["pasos"]
        ejecucion_id = await servicios.ejecutar(
            tenant, dba, workaround, conexion.id, base, {}, "web"
        )
        assert (await _esperar(tenant, ejecucion_id))["estado"] == "exitosa"

    # Poner online una base que quedó OFFLINE: el diagnóstico y los pasos corren en master.
    with sqlserver.conectar_externo(SERVIDOR, "sa", password) as c:
        c.cursor().execute(f"ALTER DATABASE [{base}] SET OFFLINE WITH ROLLBACK IMMEDIATE")
    assert base not in await servicios.bases(tenant, conexion.id)
    diag = await servicios.diagnostico(
        tenant, dba, "base_online", conexion.id, None, {"base": base}
    )
    caida = next(b for b in diag["diagnostico"]["bases"] if b["nombre"] == base)
    assert caida == {"nombre": base, "estado": "OFFLINE", "recuperable": True}
    assert diag["pasos"][0]["comando"] == f"ALTER DATABASE [{base}] SET ONLINE"
    ejecucion_id = await servicios.ejecutar(
        tenant, dba, "base_online", conexion.id, None, {"base": base}, "web"
    )
    assert (await _esperar(tenant, ejecucion_id))["estado"] == "exitosa"
    assert base in await servicios.bases(tenant, conexion.id)

    # KILL de una sesión bloqueante elegida en el diagnóstico.
    bloqueadora = sqlserver.abrir_externo(SERVIDOR, "sa", password, base)
    bloqueadora.autocommit = False
    bloqueadora.cursor().execute("UPDATE dbo.t SET x = 1 WHERE id = 1")

    def esperar_lock():
        try:
            with sqlserver.conectar_externo(SERVIDOR, "sa", password, base) as c:
                c.cursor().execute("SET LOCK_TIMEOUT 15000; SELECT * FROM dbo.t").fetchall()
        except Exception:
            pass

    hilo = threading.Thread(target=esperar_lock)
    hilo.start()
    try:
        bloqueadores = []
        for _ in range(30):
            diag = await servicios.diagnostico(tenant, dba, "kill_bloqueos", conexion.id, None, {})
            bloqueadores = diag["diagnostico"]["bloqueadores"]
            if bloqueadores:
                break
            time.sleep(0.2)
        sid = bloqueadores[0]["sid"]
        assert bloqueadores[0]["raiz"] and diag["pasos"] == []
        with pytest.raises(ErrorDominio) as error:
            await servicios.ejecutar(
                tenant, dba, "kill_bloqueos", conexion.id, None, {"sids": [9999]}, "web"
            )
        assert error.value.codigo == "WKR_EJE_001"
        ejecucion_id = await servicios.ejecutar(
            tenant, dba, "kill_bloqueos", conexion.id, None, {"sids": [sid]}, "mobile"
        )
        resultado = await _esperar(tenant, ejecucion_id)
        assert resultado["estado"] == "exitosa" and resultado["origen"] == "mobile"
    finally:
        hilo.join()
        with contextlib.suppress(Exception):
            bloqueadora.close()

    # Propio transaccional: una falla revierte todo.
    propio = await servicios.guardar_propio(
        tenant,
        dba,
        None,
        Propio(
            "",
            "",
            "Marcar y fallar",
            "Prueba de reversión",
            "mantenimiento",
            "info",
            ("DEV", "TESTING"),
            ("DBA",),
            True,
            None,
            0,
            None,
            ("UPDATE dbo.t SET x = 99", "SELECT 1 / 0"),
        ),
        (conexion.id, base),
    )
    assert propio["estado"] == "pendiente_sandbox" and propio["version_actual"] == 1
    with pytest.raises(ErrorDominio) as error:
        await servicios.guardar_propio(
            tenant,
            dba,
            None,
            Propio(
                "",
                "",
                "Mal escrito",
                "x",
                "mantenimiento",
                "info",
                ("DEV",),
                ("DBA",),
                True,
                None,
                0,
                None,
                ("SELEC * FROM dbo.t",),
            ),
            (conexion.id, base),
        )
    assert error.value.codigo == "SQL_VAL_000"
    ejecucion_id = await servicios.ejecutar(tenant, dba, propio["id"], conexion.id, base, {}, "web")
    resultado = await _esperar(tenant, ejecucion_id)
    assert resultado["estado"] == "revertida"
    assert [p["estado"] for p in resultado["pasos"]] == ["revertido", "error"]
    with sqlserver.conectar_externo(SERVIDOR, "sa", password, base) as c:
        assert c.cursor().execute("SELECT x FROM dbo.t").fetchone()[0] != 99
    assert avisos and avisos[-1][0] == ("ADMINISTRADOR", "DBA")

    # Modificar crea una versión nueva.
    modificado = await servicios.guardar_propio(
        tenant,
        dba,
        propio["id"],
        Propio(
            "",
            "",
            "Marcar y fallar",
            "v2",
            "mantenimiento",
            "info",
            ("DEV",),
            ("DBA",),
            True,
            None,
            0,
            None,
            ("UPDATE dbo.t SET x = 2",),
        ),
        (conexion.id, base),
    )
    assert modificado["version_actual"] == 2 and len(modificado["versiones"]) == 2

    # Ventana de mantenimiento: fuera de ella hace falta la autorización de otro usuario.
    await conexiones.modificar(
        tenant,
        dba,
        conexion.id,
        DatosConexion(
            "LOCAL",
            HOST,
            PUERTO,
            None,
            "DEV",
            "sa",
            None,
            ventana_inicio=hora(0, 0),
            ventana_fin=hora(0, 1),
        ),
    )
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(tenant, dba, "update_statistics", conexion.id, base, {}, "web")
    assert error.value.codigo == "WKR_VEN_000"
    await servicios.solicitar_autorizacion(tenant, dba, "update_statistics", conexion.id, "Urgente")
    [pedido] = await servicios.autorizaciones(tenant)
    with pytest.raises(ErrorDominio) as error:
        await servicios.resolver_autorizacion(tenant, dba, pedido["id"], True)
    assert error.value.codigo == "WKR_AUT_001"
    await servicios.resolver_autorizacion(tenant, otro, pedido["id"], True)
    with pytest.raises(ErrorDominio) as error:
        # Sin estadísticas desactualizadas no hay pasos, pero la autorización se usó.
        await servicios.ejecutar(tenant, dba, "update_statistics", conexion.id, base, {}, "web")
    assert error.value.codigo == "WKR_EJE_002"
    assert (await servicios.autorizaciones(tenant)) == []

    # El usuario sin PROD habilitado no ejecuta en PROD; con PROD exige segundo factor.
    await conexiones.modificar(
        tenant,
        dba,
        conexion.id,
        DatosConexion("LOCAL", HOST, PUERTO, None, "PROD", "sa", None),
    )
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(tenant, dba, "checkpoint", conexion.id, base, {}, "web")
    assert error.value.codigo == "AMB_VAL_000"
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(tenant, otro, "checkpoint", conexion.id, base, {}, "web")
    assert error.value.codigo == "USR_PER_000"
    # Propio no validado en Sandbox: no aplica a PROD.
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(
            tenant,
            replace(dba, segundo_factor_en=time.time()),
            propio["id"],
            conexion.id,
            base,
            {},
            "web",
        )
    assert error.value.codigo == "WKR_AMB_000"

    # Automatización: solo integrados aptos; sobre PROD exige segundo factor.
    with pytest.raises(ErrorDominio) as error:
        await servicios.crear_automatizacion(tenant, dba, "log", "kill_bloqueos", None)
    assert error.value.codigo == "WKR_AUT_002"
    with pytest.raises(ErrorDominio) as error:
        await servicios.crear_automatizacion(tenant, dba, "log", "shrink_log", None)
    assert error.value.codigo == "AMB_VAL_000"
    automatizacion = await servicios.crear_automatizacion(
        tenant, replace(dba, segundo_factor_en=time.time()), "log", "shrink_log", conexion.id
    )
    await servicios.Automatizador().alerta_disparada(
        tenant,
        await conexiones.obtener(tenant, conexion.id),
        type("A", (), {"clave": f"evento:log:{base}", "id": 7})(),
    )
    [automatica] = [e for e in await servicios.ejecuciones(tenant) if e["origen"] == "automatica"]
    assert (await _esperar(tenant, automatica["id"]))["alerta_id"] == 7
    await servicios.eliminar_automatizacion(tenant, dba, automatizacion["id"])

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    transacciones = list(cliente[tenant.base_mongodb]["bitacora_transacciones"].find())
    assert len(transacciones) >= 5 and any(t["detalle"]["mobile"] for t in transacciones)
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
