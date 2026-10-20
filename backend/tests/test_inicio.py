import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errores import ErrorDominio
from app.modulos.inicio import repositorios, servicios
from app.modulos.monitor import repositorios as monitor
from app.modulos.workarounds import integrados
from tests.test_conexiones import (
    TENANT,
    _actor,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)

AHORA = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)  # 12:00 en Buenos Aires
MES = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)  # 1/10 00:00 en Buenos Aires


def _sin_zona(valor: datetime) -> datetime:
    return valor.astimezone(UTC).replace(tzinfo=None)


def test_inicio_de_mes_en_hora_local():
    assert servicios.inicio_de_mes(AHORA) == MES
    # El 1/11 a las 01:00 UTC todavía es octubre en Buenos Aires.
    assert servicios.inicio_de_mes(datetime(2026, 11, 1, 1, 0, tzinfo=UTC)) == MES


def test_disponibilidad():
    periodo = AHORA - MES
    conexiones = [
        {"id": "a", "creado_en": _sin_zona(MES - timedelta(days=30))},
        {"id": "b", "creado_en": _sin_zona(AHORA - timedelta(hours=10))},
        {"id": "c", "creado_en": _sin_zona(AHORA + timedelta(hours=1))},  # sin período
    ]
    caidas = [
        # Caída que empezó el mes anterior: cuenta desde el inicio del mes (2 h).
        {
            "conexion_id": "a",
            "desde": _sin_zona(MES - timedelta(hours=5)),
            "hasta": _sin_zona(MES + timedelta(hours=2)),
        },
        # Superpuesta con la anterior: no se cuenta dos veces.
        {
            "conexion_id": "a",
            "desde": _sin_zona(MES + timedelta(hours=1)),
            "hasta": _sin_zona(MES + timedelta(hours=3)),
        },
        # Caída abierta de 1 h en una instancia registrada hace 10 h.
        {"conexion_id": "b", "desde": _sin_zona(AHORA - timedelta(hours=1)), "hasta": None},
    ]
    esperado_a = 100 * (1 - 3 * 3600 / periodo.total_seconds())
    esperado_b = 100 * (1 - 1 / 10)
    assert servicios.disponibilidad(conexiones, caidas, MES, AHORA) == round(
        (esperado_a + esperado_b) / 2, 1
    )
    assert servicios.disponibilidad([], [], MES, AHORA) is None
    assert servicios.disponibilidad(conexiones[2:], [], MES, AHORA) is None


@pytest.fixture
def datos(entorno, monkeypatch, redis_falso):  # noqa: F811
    conexiones = [
        {"id": "p1", "ambiente": "PROD", "creado_en": _sin_zona(MES + timedelta(days=2))},
        {"id": "p2", "ambiente": "PROD", "creado_en": _sin_zona(MES - timedelta(days=60))},
        {"id": "d1", "ambiente": "DEV", "creado_en": _sin_zona(MES - timedelta(days=60))},
    ]
    alertas = [
        {"ambiente": "PROD", "severidad": "critical", "cantidad": 1},
        {"ambiente": "PROD", "severidad": "warning", "cantidad": 2},
        {"ambiente": "DEV", "severidad": "warning", "cantidad": 4},
    ]
    caidas = [{"conexion_id": "p2", "desde": _sin_zona(AHORA - timedelta(hours=2)), "hasta": None}]
    propios = [
        {"ambientes": ["PROD"], "creado_en": _sin_zona(MES + timedelta(days=1))},
        {"ambientes": ["DEV", "TESTING"], "creado_en": _sin_zona(MES - timedelta(days=1))},
    ]
    llamadas = {}

    async def latencia(base, ids, desde):
        llamadas["latencia"] = (ids, desde)
        return 218.4

    async def pendientes(base, tenant_id, ambientes, desde):
        llamadas["pendientes"] = ambientes
        return [("UPDATE dbo.t SET a = 1 WHERE b = 2", "Ventas"), ("SELECT 1", "Ventas")]

    monkeypatch.setattr(repositorios, "conexiones", lambda b, t: conexiones)
    monkeypatch.setattr(repositorios, "alertas_activas", lambda b, t: alertas)
    monkeypatch.setattr(repositorios, "caidas", lambda b, t, desde, hasta: caidas)
    monkeypatch.setattr(repositorios, "workarounds_propios", lambda b, t: propios)
    monkeypatch.setattr(repositorios, "latencia_p95", latencia)
    monkeypatch.setattr(repositorios, "sql_pendientes_de_promocion", pendientes)
    return {**entorno, "redis": redis_falso, "llamadas": llamadas}


async def _lectura(redis, conexion_id, estado, sesiones=0, esperas=()):
    await redis.set(
        f"monitor:{TENANT.id}:{conexion_id}",
        json.dumps(
            {
                "estado": estado,
                "valores": {"sesiones": sesiones},
                "bloqueos": [{"sid": 1, "espera_s": e} for e in esperas],
            }
        ),
    )


async def test_resumen_por_ambiente_y_rol(datos):
    await _lectura(datos["redis"], "p1", "ok", 300, (45, 12))
    await _lectura(datos["redis"], "p2", "sin_conexion", 99, (60,))
    await _lectura(datos["redis"], "d1", "ok", 20)

    prod = await servicios.resumen(TENANT, _actor("DBA"), "PROD", AHORA)
    assert prod["instancias"] == {"total": 2, "nuevas_mes": 1}
    assert prod["alertas"] == {"activas": 3, "criticas": 1}
    salud = prod["salud"]
    assert (salud["online"], salud["total"], salud["sesiones"], salud["bloqueos_prolongados"]) == (
        1,
        2,
        300,
        1,
    )
    assert salud["latencia_p95"] == 218 and datos["llamadas"]["latencia"][0] == ["p1", "p2"]
    assert salud["disponibilidad"] < 100
    assert prod["workarounds"]["nuevos_mes"] == 1
    # Solo cuentan las validaciones que modifican datos (el SELECT no se promueve).
    assert prod["pendientes_promocion"] == 1 and datos["llamadas"]["pendientes"] == ["PROD"]

    todos = await servicios.resumen(TENANT, _actor("DBA"), servicios.TODOS, AHORA)
    assert todos["instancias"]["total"] == 3 and todos["alertas"]["activas"] == 7
    # Los integrados aplican a todos los ambientes; los propios, a los suyos.
    n = len(integrados.CATALOGO)
    assert (prod["workarounds"]["disponibles"], todos["workarounds"]["disponibles"]) == (
        n + 1,
        n + 2,
    )
    assert todos["salud"]["sesiones"] == 320

    consulta = await servicios.resumen(TENANT, _actor("CONSULTA"), "DEV", AHORA)
    assert consulta["workarounds"] is None and consulta["pendientes_promocion"] is None
    assert consulta["salud"]["disponibilidad"] == 100.0

    with pytest.raises(ErrorDominio) as error:
        await servicios.resumen(TENANT, _actor(), "QA", AHORA)
    assert error.value.codigo == "USR_VAL_002"


async def test_resumen_tolera_fallas_de_redis_y_mongo(datos, monkeypatch):
    async def fallar(*args):
        raise ConnectionError("caído")

    monkeypatch.setattr(monitor, "obtener_estado", fallar)
    monkeypatch.setattr(repositorios, "latencia_p95", fallar)
    r = await servicios.resumen(TENANT, _actor(), "PROD", AHORA)
    assert r["salud"]["online"] == 0 and r["salud"]["latencia_p95"] is None


async def test_api_y_pagina(datos):
    navegador = await _navegador(datos, "CONSULTA")
    r = navegador.get("/api/inicio?ambiente=DEV")
    assert r.status_code == 200 and r.json()["workarounds"] is None
    assert navegador.get("/api/inicio?ambiente=OTRO").status_code == 422
    pagina = navegador.get("/")
    assert "Consultar en lenguaje natural" in pagina.text
    assert "Ejecutar workaround" not in pagina.text  # Consulta no ve Workarounds

    dba = await _navegador(datos, "DBA")
    assert "Validar consulta en Sandbox" in dba.get("/").text
