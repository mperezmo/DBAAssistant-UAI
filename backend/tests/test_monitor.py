import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pyodbc
import pytest

from app.core import cifrado
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.modulos.monitor import lectura, recolector, repositorios, servicios
from app.modulos.monitor.entidades import METRICAS, Lectura, Umbral
from tests.test_conexiones import (
    CABECERA,
    TENANT,
    _actor,
    _datos,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)

# --- Lectura de DMVs ----------------------------------------------------------------


class CursorFalso:
    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.ejecutadas = []
        self._actual = None

    def execute(self, consulta):
        self.ejecutadas.append(consulta)
        self._actual = self.respuestas[consulta]
        return self

    def fetchone(self):
        return self._actual[0] if self._actual else None

    def fetchall(self):
        return self._actual


class ConexionFalsa:
    def __init__(self, respuestas):
        self.cursor_falso = CursorFalso(respuestas)

    def cursor(self):
        return self.cursor_falso


def _respuestas(logins=100, cpu=42):
    return {
        lectura.CONSULTA_CPU: [(cpu,)],
        lectura.CONSULTA_MEMORIA: [(1000, 250)],
        lectura.CONSULTA_VOLUMENES: [("C:\\", 100 * lectura.GB, 40 * lectura.GB), (None, 10, 9)],
        lectura.CONSULTA_ACTIVIDAD: [(7, 215, logins)],
        lectura.CONSULTA_BLOQUEOS: [(87, 52, "ERP", "KEY: 5:72", "LCK_M_X", 28_400)],
        lectura.CONSULTA_DEADLOCKS: [(2,)],
        lectura.CONSULTA_COSTOSAS: [(14_820, 124, "  EXEC sp_Facturar  ")],
    }


def test_leer_calcula_metricas_tasas_y_bloqueos():
    estado = lectura.EstadoLectura()
    conexion = ConexionFalsa(_respuestas())

    primera = lectura.leer(conexion, estado, 1000.0)
    conexion.cursor_falso.respuestas = _respuestas(logins=130, cpu=None)
    segunda = lectura.leer(conexion, estado, 1003.0)

    assert primera.valores == {
        "cpu": 42.0,
        "memoria": 75.0,
        "disco": 60.0,
        "latencia": 215.0,
        "sesiones": 7.0,
        "bloqueos": 1.0,
        "deadlocks": 2.0,
        "conexiones_seg": None,
    }
    assert primera.volumenes[0] == {
        "volumen": "C:\\",
        "total_gb": 100.0,
        "libre_gb": 40.0,
        "uso": 60.0,
    }
    assert primera.volumenes[1]["volumen"] == "/"
    assert primera.bloqueos == [
        {
            "sid": 87,
            "bloqueador": 52,
            "base": "ERP",
            "recurso": "KEY: 5:72",
            "modo": "X",
            "espera_s": 28.4,
        }
    ]
    assert segunda.valores["conexiones_seg"] == 10.0 and segunda.valores["cpu"] is None
    # Los deadlocks se leen una vez por minuto.
    assert conexion.cursor_falso.ejecutadas.count(lectura.CONSULTA_DEADLOCKS) == 1


def test_leer_descarta_la_tasa_si_la_instancia_se_reinicio():
    estado = lectura.EstadoLectura(logins=500, logins_en=1.0)
    resultado = lectura.leer(ConexionFalsa(_respuestas(logins=3)), estado, 4.0)
    assert resultado.valores["conexiones_seg"] is None and estado.logins == 3


def test_modo_lock_y_consultas_costosas():
    assert lectura._modo_lock("PAGEIOLATCH_SH") == "PAGEIOLATCH_SH"
    assert lectura._modo_lock(None) == ""
    assert lectura.consultas_costosas(ConexionFalsa(_respuestas())) == [
        {"cpu_ms": 14_820, "ejecuciones": 124, "texto": "EXEC sp_Facturar"}
    ]


# --- Servicios y API ----------------------------------------------------------------


@pytest.fixture
def monitor(entorno, monkeypatch):  # noqa: F811
    guardados: dict[str, dict[str, Umbral]] = {}
    series = []

    def listar_umbrales(base, tenant_id, conexion_id):
        return dict(guardados.get(conexion_id, {}))

    def guardar_umbrales(base, tenant_id, conexion_id, umbrales):
        guardados[conexion_id] = {u.metrica: u for u in umbrales}

    async def serie(base, conexion_id, metricas, desde, intervalo):
        series.append((conexion_id, metricas, intervalo))
        if conexion_id == "falla":
            raise RuntimeError("mongo caído")
        return [{"ts": "2026-10-20T00:00:00+00:00", **dict.fromkeys(metricas, 1.0)}]

    monkeypatch.setattr(repositorios, "listar_umbrales", listar_umbrales)
    monkeypatch.setattr(repositorios, "guardar_umbrales", guardar_umbrales)
    monkeypatch.setattr(repositorios, "serie", serie)
    return {**entorno, "umbrales": guardados, "series": series}


async def test_estado_actual_sin_datos_y_con_lectura(monitor):
    conexion = await _crear(monitor)

    vacio = await servicios.estado_actual(TENANT, conexion.id)
    assert vacio["estado"] == "sin_datos"
    assert vacio["conexion"]["servidor"] == "sql01.acme.local\\ERP"
    assert {u["metrica"]: u["warning"] for u in vacio["umbrales"]}["cpu"] == 70

    await repositorios.guardar_estado(
        str(TENANT.id), conexion.id, {"estado": "ok", "valores": {"cpu": 5}}
    )
    actual = await servicios.estado_actual(TENANT, conexion.id)
    assert actual["estado"] == "ok" and actual["valores"] == {"cpu": 5}
    assert actual["unidades"]["latencia"] == "ms"


async def _crear(monitor):
    from app.modulos.conexiones import servicios as conexiones

    return await conexiones.crear(TENANT, _actor(), _datos())


async def test_serie_valida_rango_y_metricas(monitor):
    conexion = await _crear(monitor)

    resultado = await servicios.serie(TENANT, conexion.id, "24h", ["cpu", "memoria"])
    assert resultado["intervalo"] == 900 and resultado["puntos"][0]["cpu"] == 1.0

    for rango, metricas in (("2h", ["cpu"]), ("1h", ["otra"]), ("1h", [])):
        with pytest.raises(ErrorDominio) as error:
            await servicios.serie(TENANT, conexion.id, rango, metricas)
        assert error.value.codigo == "USR_VAL_002"


async def test_serie_informa_falla_de_mongo(monitor, monkeypatch):
    conexion = await _crear(monitor)
    monitor["repo"].conexiones["falla"] = replace(conexion, id="falla")
    monkeypatch.setattr(servicios.conexiones, "obtener", lambda t, c: _async(conexion))

    with pytest.raises(ErrorDominio) as error:
        await servicios.serie(TENANT, "falla", "1h", ["cpu"])
    assert error.value.codigo == "MNT_DAT_000"


async def _async(valor):
    return valor


@pytest.mark.parametrize(
    ("umbral", "codigo"),
    [
        (Umbral("cpu", True, 90, 80), "MNT_CFG_000"),
        (Umbral("cpu", True, 80, 80), "MNT_CFG_000"),
        (Umbral("cpu", True, -1, 80), "MNT_CFG_000"),
        (Umbral("cpu", True, 80, None), "USR_VAL_002"),
        (Umbral("inexistente"), "USR_VAL_002"),
    ],
)
async def test_umbrales_invalidos(monitor, umbral, codigo):
    conexion = await _crear(monitor)
    with pytest.raises(ErrorDominio) as error:
        await servicios.guardar_umbrales(TENANT, _actor(), conexion.id, [umbral])
    assert error.value.codigo == codigo


async def test_guardar_umbrales_audita_y_exige_rol(monitor):
    conexion = await _crear(monitor)

    with pytest.raises(ErrorDominio) as error:
        await servicios.guardar_umbrales(TENANT, _actor("CONSULTA"), conexion.id, [])
    assert error.value.codigo == "USR_PER_000"
    with pytest.raises(ErrorDominio):
        await servicios.guardar_umbrales(
            TENANT, _actor(), conexion.id, [Umbral("cpu"), Umbral("cpu")]
        )

    resultado = await servicios.guardar_umbrales(
        TENANT, _actor(), conexion.id, [Umbral("cpu", True, 50, 60), Umbral("sesiones", False)]
    )

    por_metrica = {u.metrica: u for u in resultado}
    assert len(resultado) == len(METRICAS)
    assert por_metrica["cpu"].critical == 60 and por_metrica["memoria"].warning == 80
    assert por_metrica["sesiones"].habilitada is False
    accion, detalle = monitor["eventos"][-1]
    assert accion == "monitor.umbrales_modificados" and detalle["alias"] == "ERP-PROD-01"
    assert detalle["anteriores"][0]["critical"] == 85 and detalle["nuevos"][0]["critical"] == 60


async def test_consultas_costosas_usa_cache(monitor, monkeypatch):
    conexion = await _crear(monitor)
    lecturas = []

    def leer(c):
        lecturas.append(c.id)
        return [{"cpu_ms": 1, "ejecuciones": 1, "texto": "SELECT 1"}]

    monkeypatch.setattr(servicios, "_leer_costosas", leer)

    assert await servicios.consultas_costosas(TENANT, conexion.id) == [
        {"cpu_ms": 1, "ejecuciones": 1, "texto": "SELECT 1"}
    ]
    await servicios.consultas_costosas(TENANT, conexion.id)
    assert lecturas == [conexion.id]


def test_leer_costosas_informa_la_falla(entorno, monkeypatch):  # noqa: F811
    conexion = replace(
        _conexion_guardada(), password_sql_cifrada=cifrado.cifrar("x"), host="no-existe"
    )

    def fallar(*_, **__):
        raise pyodbc.OperationalError("08001", "sin red")

    monkeypatch.setattr(servicios.sqlserver, "abrir_externo", fallar)
    with pytest.raises(ErrorDominio) as error:
        servicios._leer_costosas(conexion)
    assert error.value.codigo == "MNT_DAT_000"


def _conexion_guardada():
    from app.modulos.conexiones.entidades import Conexion

    return Conexion(str(uuid4()), str(TENANT.id), "A", "h", None, None, "DEV", "u", "")


async def test_api_monitor_por_rol(monitor):
    conexion = await _crear(monitor)
    consulta = await _navegador(monitor, "CONSULTA")

    listado = consulta.get("/api/monitor").json()
    assert listado["puede_configurar"] is False
    assert listado["conexiones"][0]["alias"] == "ERP-PROD-01"
    assert consulta.get(f"/api/monitor/{conexion.id}").json()["estado"] == "sin_datos"
    serie = consulta.get(f"/api/monitor/{conexion.id}/serie?rango=1h&metricas=cpu,sesiones")
    assert serie.json()["puntos"][0]["sesiones"] == 1.0
    assert len(consulta.get(f"/api/monitor/{conexion.id}/umbrales").json()["umbrales"]) == 8
    denegado = consulta.put(
        f"/api/monitor/{conexion.id}/umbrales", json={"umbrales": []}, headers=CABECERA
    )
    assert denegado.json()["codigo"] == "USR_PER_000"
    assert consulta.get("/api/monitor/no-es-un-id").json()["codigo"] == "CON_DB_002"
    assert consulta.get("/monitor").status_code == 200

    dba = await _navegador(monitor, "DBA")
    guardado = dba.put(
        f"/api/monitor/{conexion.id}/umbrales",
        json={"umbrales": [{"metrica": "cpu", "warning": 60, "critical": 95}]},
        headers=CABECERA,
    )
    assert guardado.json()["umbrales"][0] == {
        "metrica": "cpu",
        "habilitada": True,
        "warning": 60,
        "critical": 95,
    }


async def test_api_consultas_costosas(monitor, monkeypatch):
    conexion = await _crear(monitor)
    monkeypatch.setattr(servicios, "_leer_costosas", lambda c: [])
    navegador = await _navegador(monitor, "DBA")

    assert navegador.get(f"/api/monitor/{conexion.id}/consultas-costosas").json() == {
        "consultas": []
    }


# --- Recolector ---------------------------------------------------------------------


@pytest.fixture
def recoleccion(entorno, monkeypatch):  # noqa: F811
    muestras = []
    lecturas = {"falla": False}

    async def insertar(base, conexion_id, ts, valores):
        muestras.append((conexion_id, valores))

    class Abierta:
        cerrada = False

        def close(self):
            self.cerrada = True

    abiertas = []

    def abrir(servidor, usuario, password, timeout):
        abiertas.append(Abierta())
        return abiertas[-1]

    def leer(conexion, estado, ahora):
        if lecturas["falla"]:
            raise pyodbc.OperationalError("08S01", "Communication link failure")
        return Lectura({"cpu": 10.0}, [], [])

    monkeypatch.setattr(repositorios, "insertar_muestra", insertar)
    monkeypatch.setattr(recolector.sqlserver, "abrir_externo", abrir)
    monkeypatch.setattr(recolector.lectura, "leer", leer)
    monkeypatch.setattr(
        recolector.tenants, "listar", lambda: [TENANT, replace(TENANT, estado="baja")]
    )
    return {**entorno, "muestras": muestras, "abiertas": abiertas, "lecturas": lecturas}


async def _ciclo(r):
    await r.ciclo()
    while r._tareas:
        await asyncio.sleep(0.01)


async def test_recolector_lee_guarda_y_marca_la_caida(recoleccion, redis_falso):
    from app.modulos.conexiones import servicios as conexiones

    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    r = recolector.Recolector()

    await _ciclo(r)
    estado = json.loads(await redis_falso.get(f"monitor:{TENANT.id}:{conexion.id}"))
    assert estado["estado"] == "ok" and estado["valores"] == {"cpu": 10.0}
    assert recoleccion["muestras"] == [(conexion.id, {"cpu": 10.0})]

    # Segundo ciclo: reutiliza la conexión abierta.
    await _ciclo(r)
    assert len(recoleccion["abiertas"]) == 1
    estado = json.loads(await redis_falso.get(f"monitor:{TENANT.id}:{conexion.id}"))

    recoleccion["lecturas"]["falla"] = True
    await _ciclo(r)
    caida = json.loads(await redis_falso.get(f"monitor:{TENANT.id}:{conexion.id}"))
    assert caida["estado"] == "sin_conexion" and caida["error"] == "CON_DB_003"
    assert caida["valores"] == {"cpu": 10.0} and caida["ultima_lectura"] == estado["ultima_lectura"]
    assert recoleccion["abiertas"][0].cerrada

    recoleccion["lecturas"]["falla"] = False
    await _ciclo(r)
    assert json.loads(await redis_falso.get(f"monitor:{TENANT.id}:{conexion.id}"))["estado"] == "ok"
    assert len(recoleccion["abiertas"]) == 2

    # Al eliminar la conexión se cierra en el siguiente refresco del catálogo.
    await conexiones.eliminar(TENANT, _actor(segundo_factor_en=10**10), conexion.id)
    r._catalogo_en = 0
    await _ciclo(r)
    assert recoleccion["abiertas"][1].cerrada and r._abiertas == {}
    r.detener()


async def test_recolector_reabre_si_cambian_los_datos(recoleccion):
    from app.modulos.conexiones import servicios as conexiones

    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    r = recolector.Recolector()
    await _ciclo(r)
    await conexiones.modificar(TENANT, _actor(), conexion.id, _datos(ambiente="DEV", puerto=1450))
    r._catalogo_en = 0
    await _ciclo(r)

    assert len(recoleccion["abiertas"]) == 2 and recoleccion["abiertas"][0].cerrada
    r.detener()


async def test_recolector_tolera_fallas_de_guardado(recoleccion, monkeypatch):
    from app.modulos.conexiones import servicios as conexiones

    await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))

    async def fallar(*_):
        raise RuntimeError("mongo caído")

    monkeypatch.setattr(repositorios, "insertar_muestra", fallar)
    r = recolector.Recolector()
    await _ciclo(r)
    assert r._en_curso == set()
    r.detener()


async def test_recolector_tolera_tenants_con_error(recoleccion, monkeypatch):
    def fallar(base, tenant_id):
        raise pyodbc.OperationalError("08001", "base no disponible")

    monkeypatch.setattr(recolector.conexiones, "listar", fallar)
    assert recolector.Recolector._leer_catalogo() == []


async def test_candado_un_solo_recolector(redis_falso):
    uno, otro = recolector.Recolector(), recolector.Recolector()

    assert await uno._tomar_candado() is True
    assert await otro._tomar_candado() is False
    assert await uno._tomar_candado() is True
    uno.detener()
    otro.detener()


async def test_ejecutar_respeta_el_candado_y_sobrevive_a_fallas(redis_falso, monkeypatch):
    r = recolector.Recolector()
    ciclos = []

    async def ciclo():
        ciclos.append(1)
        raise RuntimeError("falla inesperada")

    esperas = []

    async def esperar(segundos):
        esperas.append(segundos)
        if len(esperas) == 2:
            raise asyncio.CancelledError

    monkeypatch.setenv("MONITOR_INTERVALO_SEGUNDOS", "3")
    obtener_configuracion.cache_clear()
    monkeypatch.setattr(r, "ciclo", ciclo)
    monkeypatch.setattr(recolector.asyncio, "sleep", esperar)

    with pytest.raises(asyncio.CancelledError):
        await r.ejecutar()
    assert ciclos == [1, 1] and 0 < esperas[0] <= 3
    r.detener()
    obtener_configuracion.cache_clear()


async def test_repositorio_de_estado_y_cache(redis_falso):
    assert await repositorios.obtener_estado("t", "c") is None
    await repositorios.guardar_en_cache("clave", [1, 2])
    assert await repositorios.obtener_de_cache("clave") == [1, 2]
    assert 0 < await redis_falso.ttl("clave") <= obtener_configuracion().cache_ttl_metricas


def test_umbral_por_defecto():
    assert Umbral.por_defecto("latencia") == Umbral("latencia", True, 300, 450)
    assert Umbral.por_defecto("sesiones") == Umbral("sesiones", True, None, None)
