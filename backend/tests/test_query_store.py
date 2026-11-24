"""Consultas costosas y su tendencia desde Query Store, su uso en Buenas Prácticas, los
contadores de Inicio y los permisos que informa la prueba de conexión."""

from types import SimpleNamespace

import pyodbc
import pytest

from app.core.errores import ErrorDominio
from app.modulos.buenas_practicas import chequeos
from app.modulos.conexiones import repositorios as conexiones_repo
from app.modulos.monitor import query_store as qs
from app.modulos.monitor import servicios
from tests.test_conexiones import TENANT, _navegador
from tests.test_monitor import _crear, entorno, monitor  # noqa: F401 (fixtures)

AHORA = "2026-11-16T18"


def _tramo(hora: str, ejecuciones: int, duracion_ms: float, plan_id: int = 1) -> dict:
    return {
        "plan_id": plan_id,
        "hora": hora,
        "ejecuciones": ejecuciones,
        "cpu": duracion_ms * 1000 * ejecuciones / 2,
        "duracion": duracion_ms * 1000 * ejecuciones,
        "lecturas": 10.0 * ejecuciones,
    }


def test_tendencia_regresion_y_cambio_de_plan():
    # Antes: 2 ms por ejecución; en las últimas 24 h, 5 ms y con un plan nuevo.
    filas = [_tramo("2026-11-12T10", 50, 2.0), _tramo("2026-11-16T10", 40, 5.0, plan_id=2)]
    marcas = qs.tendencia(filas, AHORA, "duracion")
    assert marcas == {
        "empeoro": True,
        "cambio_plan": True,
        "medida": "duracion",
        "antes": 2.0,
        "ahora": 5.0,
    }
    # Con pocas ejecuciones no se marca la regresión.
    pocas = [_tramo("2026-11-12T10", 5, 2.0), _tramo("2026-11-16T10", 40, 5.0)]
    assert not qs.tendencia(pocas, AHORA, "cpu")["empeoro"]
    # Sin historia previa no hay cambio de plan; por ejecuciones se mide la duración.
    nuevas = qs.tendencia([_tramo("2026-11-16T10", 40, 5.0)], AHORA, "ejecuciones")
    assert nuevas["medida"] == "duracion" and not nuevas["cambio_plan"] and nuevas["antes"] is None
    assert qs.tendencia([], AHORA, "lecturas")["ahora"] is None


def test_puntos_por_hora_y_por_dia():
    filas = [
        _tramo("2026-11-15T10", 10, 2.0),
        _tramo("2026-11-15T11", 30, 4.0),
        _tramo("2026-11-16T10", 10, 1.0),
    ]
    assert qs._puntos(filas, "duracion", "2026-11-15T11", por_dia=False) == [
        {"t": "2026-11-15T11", "v": 4.0},
        {"t": "2026-11-16T10", "v": 1.0},
    ]
    assert qs._puntos(filas, "ejecuciones", "2026-11-01T00", por_dia=True) == [
        {"t": "2026-11-15", "v": 40},
        {"t": "2026-11-16", "v": 10},
    ]


class _Cursor:
    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.actual = []
        self.ejecutadas = []

    def execute(self, consulta, *parametros):
        self.ejecutadas.append(consulta)
        for clave, valor in self.respuestas.items():
            if clave in consulta:
                if isinstance(valor, Exception):
                    raise valor
                self.actual = valor
                return self
        raise AssertionError(consulta[:80])

    def fetchall(self):
        return self.actual

    def fetchone(self):
        return self.actual[0] if self.actual else None


RESPUESTAS = {
    "ProductMajorVersion": [(16,)],
    "query_store_options": [("READ_WRITE", "READ_WRITE", 0, 12, 1024)],
    "SYSUTCDATETIME": [(AHORA,)],
    f"SELECT TOP ({qs.MAXIMO_CONSULTAS})": [(7, 40, 200_000.0, 400_000.0, 800.0, 2)],
    "GROUP BY s.query_id, s.plan_id, s.hora": [
        (7, 1, "2026-11-12T10", 50, 50_000.0, 100_000.0, 500.0),
        (7, 2, "2026-11-16T10", 40, 200_000.0, 400_000.0, 800.0),
    ],
    "query_sql_text": [(7, " SELECT * FROM dbo.pedidos ", "dbo", "sp_Pedidos")],
    "FROM sys.query_store_plan\nWHERE query_id": [
        (1, 0, "2026-11-01T00:00:00", "2026-11-12T10:30:00"),
        (2, 1, "2026-11-16T09:00:00", "2026-11-16T10:30:00"),
    ],
}


def test_ranking_y_detalle():
    cursor = _Cursor(RESPUESTAS)
    [consulta] = qs.ranking(cursor, "duracion", "24h")
    assert (
        consulta["texto"] == "SELECT * FROM dbo.pedidos" and consulta["objeto"] == "dbo.sp_Pedidos"
    )
    assert consulta["duracion_ms"] == 400.0 and consulta["duracion_ms_ejecucion"] == 10.0
    assert consulta["tendencia"]["empeoro"] and consulta["tendencia"]["cambio_plan"]
    assert consulta["serie"] == [{"t": "2026-11-16T10", "v": 10.0}]
    # La lectura propia queda fuera del ranking.
    assert "NOT LIKE N'%dba-assistant:monitor%'" in cursor.ejecutadas[0]
    detalle = qs.detalle(_Cursor(RESPUESTAS), 7, "duracion", "7d")
    assert [p["plan_id"] for p in detalle["planes"]] == [1, 2] and detalle["planes"][1]["forzado"]
    assert detalle["planes"][1]["valor"] == 10.0 and detalle["planes"][0]["ejecuciones"] == 50
    assert qs.detalle(_Cursor({**RESPUESTAS, "query_sql_text": []}), 99, "cpu", "1h") is None


def test_estado_de_las_bases_y_consultas_de_una_base():
    respuestas = {
        **RESPUESTAS,
        "HAS_DBACCESS": [("Ventas",), ("Privada",)],
        "USE [Ventas]": [],
        "USE [Privada]": pyodbc.ProgrammingError("sin acceso"),
        "USE master": [],
    }
    conexion = SimpleNamespace(cursor=lambda: _Cursor(respuestas))
    estado = qs.estado_bases(conexion)
    assert estado["disponible"] and estado["bases"][0]["estado"] == "READ_WRITE"
    assert estado["bases"][1] == {"base": "Privada", "estado": "SIN_ACCESO"}
    viejo = SimpleNamespace(cursor=lambda: _Cursor({"ProductMajorVersion": [(12,)]}))
    assert qs.estado_bases(viejo) == {"version": 12, "disponible": False, "bases": []}
    assert qs.consultas_de_base(viejo, "cpu", "24h")["estado"] == "NO_DISPONIBLE"
    apagado = {**RESPUESTAS, "query_store_options": [("OFF", "OFF", 0, 0, 100)]}
    sin_qs = qs.consultas_de_base(SimpleNamespace(cursor=lambda: _Cursor(apagado)), "cpu", "24h")
    assert sin_qs["estado"] == "OFF" and sin_qs["consultas"] == []
    activo = qs.consultas_de_base(conexion, "cpu", "24h")
    assert activo["estado"] == "READ_WRITE" and activo["consultas"][0]["query_id"] == 7


def test_regresiones_en_buenas_practicas():
    [hallazgo] = chequeos.regresiones(_Cursor(RESPUESTAS))
    assert hallazgo["severidad"] == "alta" and hallazgo["datos"]["query_id"] == 7
    assert hallazgo["datos"]["cambio_plan"] and hallazgo["datos"]["duracion_ms_ahora"] == 10.0
    # Sin Query Store activo, o si la lectura falla, no hay regresiones.
    apagado = {**RESPUESTAS, "query_store_options": [("OFF", "OFF", 0, 0, 100)]}
    assert chequeos.regresiones(_Cursor(apagado)) == []
    falla = {**RESPUESTAS, "SYSUTCDATETIME": pyodbc.Error("timeout")}
    assert chequeos.regresiones(_Cursor(falla)) == []


async def test_servicios_y_api_de_query_store(monitor, monkeypatch):  # noqa: F811
    conexion = await _crear(monitor)
    lecturas = []

    def leer(c, funcion, *argumentos, base=None):
        lecturas.append((base, argumentos))
        if funcion is qs.estado_bases:
            return {"version": 16, "disponible": True, "bases": [{"base": "Ventas"}]}
        if funcion is qs.consultas_de_base:
            return {"estado": "READ_WRITE", "consultas": [{"query_id": 7}]}
        return funcion(SimpleNamespace(cursor=lambda: _Cursor(RESPUESTAS)))

    monkeypatch.setattr(servicios, "_leer", leer)
    estado = await servicios.estado_query_store(TENANT, conexion.id)
    assert estado["bases"] == [{"base": "Ventas"}]
    # Queda en caché: la segunda vez no se lee la instancia.
    await servicios.estado_query_store(TENANT, conexion.id)
    assert len(lecturas) == 1
    datos = await servicios.consultas_query_store(TENANT, conexion.id, "Ventas", "cpu", "7d")
    assert datos["consultas"] == [{"query_id": 7}] and lecturas[-1] == ("Ventas", ("cpu", "7d"))
    for base, metrica, periodo in (("", "cpu", "24h"), ("V", "memoria", "24h"), ("V", "cpu", "2h")):
        with pytest.raises(ErrorDominio):
            await servicios.consultas_query_store(TENANT, conexion.id, base, metrica, periodo)
    detalle = await servicios.consulta_query_store(TENANT, conexion.id, "Ventas", 7, "cpu", "24h")
    assert detalle["query_id"] == 7

    navegador = await _navegador(monitor, "LECTURA")
    base = f"/api/monitor/{conexion.id}/query-store"
    assert navegador.get(base).json()["disponible"] is True
    r = navegador.get(f"{base}/consultas?base=Ventas&metrica=duracion&periodo=1h")
    assert r.json()["consultas"] == [{"query_id": 7}]
    assert navegador.get(f"{base}/consultas?base=Ventas&metrica=x").status_code == 422
    assert navegador.get(f"{base}/consulta?base=Ventas&query_id=7").json()["query_id"] == 7

    monkeypatch.setattr(servicios, "_leer", lambda c, f, *a, base=None: None)
    with pytest.raises(ErrorDominio) as error:
        await servicios.consulta_query_store(TENANT, conexion.id, "Ventas", 8, "cpu", "24h")
    assert error.value.codigo == "MNT_VAL_000"


def test_prueba_de_conexion_informa_los_permisos_que_faltan(monkeypatch):
    class Abierta:
        def __enter__(self):
            return SimpleNamespace(
                cursor=lambda: SimpleNamespace(
                    execute=lambda consulta: SimpleNamespace(
                        fetchone=lambda: ("16.0", "Developer", 4, 1, 1, 0, 0)
                    )
                )
            )

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(conexiones_repo.sqlserver, "conectar_externo", lambda *a: Abierta())
    version, edicion, bases, faltantes = conexiones_repo.datos_de_instancia("s", "u", "p")
    assert (version, bases) == ("16.0", 4) and faltantes == ("msdb_jobs", "agente_crear")


async def test_inicio_cuenta_backups_atrasados_y_jobs_con_falla(monkeypatch):
    from app.modulos.inicio import servicios as inicio
    from app.modulos.monitor.entidades import PoliticaBackup

    estados = {
        "c1": {
            "estado": "ok",
            "valores": {"sesiones": 3},
            "backups": [
                {"base": "A", "recuperacion": "SIMPLE", "creada_min": 9e5, "full_min": None},
                {"base": "B", "recuperacion": "SIMPLE", "creada_min": 9e5, "full_min": 60},
            ],
            "jobs": [
                {"habilitado": True, "corriendo_s": None, "resultado": "falla", "hace_min": 10},
                {"habilitado": True, "corriendo_s": None, "resultado": "exito", "hace_min": 10},
            ],
        },
        # Con política semanal, un full de hace dos días está al día.
        "c2": {
            "estado": "ok",
            "backups": [
                {"base": "C", "recuperacion": "SIMPLE", "creada_min": 9e5, "full_min": 2880}
            ],
        },
    }

    async def obtener_estado(tenant_id, conexion_id):
        return estados[conexion_id]

    monkeypatch.setattr(inicio.monitor, "obtener_estado", obtener_estado)
    monkeypatch.setattr(
        inicio.monitor,
        "politicas_del_tenant",
        lambda base, t: {"c2": PoliticaBackup(full_horas=168)},
    )
    resultado = await inicio._estado_actual(TENANT, ["c1", "c2"])
    assert resultado["backups_atrasados"] == 1 and resultado["jobs_con_falla"] == 1
    assert resultado["online"] == 2 and resultado["sesiones"] == 3
