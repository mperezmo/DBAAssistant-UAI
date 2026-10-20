import asyncio
import os
import threading
import time

import pytest
from cryptography.fernet import Fernet
from pymongo import MongoClient

from app.core import cifrado
from app.core.secretos import proveedor_secretos
from app.datos import sqlserver
from app.identidad.entidades import Sesion
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import DatosConexion
from app.modulos.monitor import lectura, servicios
from app.modulos.monitor.entidades import Umbral
from app.modulos.monitor.recolector import Recolector
from app.tenants import servicios as tenants

pytestmark = pytest.mark.integracion

SERVIDOR = (
    f"{os.environ.get('SQLSERVER_HOST', 'localhost')},{os.environ.get('SQLSERVER_PUERTO', '14330')}"
)


@pytest.fixture
def clave(monkeypatch):
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    yield
    cifrado.reiniciar()


def _password() -> str:
    return proveedor_secretos().obtener("sqlserver_password")


def test_lectura_real_de_dmvs_detecta_un_bloqueo(prefijo_aislado):
    base = f"{prefijo_aislado.sqlserver_prefijo_base}_Bloqueos"
    sqlserver.crear_base_si_no_existe(base)
    with sqlserver.conectar_externo(SERVIDOR, "sa", _password(), base) as preparacion:
        preparacion.cursor().execute(
            "CREATE TABLE dbo.t (id INT PRIMARY KEY); INSERT dbo.t VALUES (1)"
        )

    bloqueadora = sqlserver.abrir_externo(SERVIDOR, "sa", _password(), base)
    bloqueadora.autocommit = False
    bloqueadora.cursor().execute("UPDATE dbo.t SET id = id WHERE id = 1")

    def esperar_lock():
        with sqlserver.conectar_externo(SERVIDOR, "sa", _password(), base) as bloqueada:
            bloqueada.cursor().execute("SET LOCK_TIMEOUT 10000; SELECT * FROM dbo.t").fetchall()

    hilo = threading.Thread(target=esperar_lock)
    hilo.start()
    try:
        estado = lectura.EstadoLectura()
        with sqlserver.conectar_externo(SERVIDOR, "sa", _password()) as monitor:
            resultado = None
            for _ in range(20):
                resultado = lectura.leer(monitor, estado, time.monotonic())
                if resultado.bloqueos:
                    break
                time.sleep(0.2)
            segunda = lectura.leer(monitor, estado, time.monotonic() + 1)
            costosas = lectura.consultas_costosas(monitor)
    finally:
        bloqueadora.rollback()
        bloqueadora.close()
        hilo.join()

    valores = resultado.valores
    assert 0 < valores["memoria"] <= 100 and 0 < valores["disco"] <= 100
    assert valores["deadlocks"] == 0 and valores["sesiones"] >= 1
    assert resultado.bloqueos[0]["base"] == base and resultado.bloqueos[0]["modo"] == "S"
    assert valores["bloqueos"] == len(resultado.bloqueos)
    assert segunda.valores["conexiones_seg"] is not None
    assert all("dba-assistant:monitor" not in c["texto"] for c in costosas)


async def test_recolector_dashboard_historico_y_umbrales(prefijo_aislado, clave, redis_falso):
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    actor = Sesion("s", "u", str(tenant.id), "acme", "admin@acme.com", "Admin", "ADMINISTRADOR", 0)
    host, puerto = SERVIDOR.split(",")
    conexion = await conexiones.crear(
        tenant,
        actor,
        DatosConexion("LOCAL-DEV", host, int(puerto), None, "DEV", "sa", _password()),
    )

    recolector = Recolector()
    for _ in range(2):
        await recolector.ciclo()
        while recolector._tareas:
            await asyncio.sleep(0.05)
    recolector.detener()

    actual = await servicios.estado_actual(tenant, conexion.id)
    assert actual["estado"] == "ok" and actual["valores"]["memoria"] > 0
    serie = await servicios.serie(tenant, conexion.id, "1h", ["memoria", "sesiones"])
    assert serie["puntos"] and serie["puntos"][-1]["memoria"] > 0
    assert isinstance(await servicios.consultas_costosas(tenant, conexion.id), list)

    await servicios.guardar_umbrales(
        tenant, actor, conexion.id, [Umbral("cpu", True, 50, 60), Umbral("sesiones", False)]
    )
    umbrales = {u.metrica: u for u in await servicios.obtener_umbrales(tenant, conexion.id)}
    assert umbrales["cpu"].critical == 60 and umbrales["sesiones"].habilitada is False

    # Al eliminar la conexión se borra su configuración de métricas.
    await conexiones.eliminar(tenant, actor, conexion.id)
    with sqlserver.conectar(tenant.base_sqlserver) as base:
        assert base.cursor().execute("SELECT COUNT(*) FROM dbo.umbral_metrica").fetchone()[0] == 0

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    opciones = cliente[tenant.base_mongodb]["metricas"].options()
    assert opciones["expireAfterSeconds"] == 30 * 24 * 60 * 60
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
