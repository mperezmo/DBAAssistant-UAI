import subprocess
from types import SimpleNamespace

import pyodbc
import pytest
from docker.errors import NotFound
from fastapi.testclient import TestClient

from app.sandbox_servicio import app as modulo_app
from app.sandbox_servicio import config, contenedores, ejecucion, estructura

TOKEN = {"X-Sandbox-Token": "secreto"}


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setenv("SECRETO_SANDBOX_TOKEN", "secreto")
    monkeypatch.setenv("SECRETO_SANDBOX_SA_PASSWORD", "Sa#Prueba1")
    config.configuracion.cache_clear()
    yield
    config.configuracion.cache_clear()


# --- Contenedores -------------------------------------------------------------------


class Contenedor:
    def __init__(self, status="running"):
        self.status = status
        self.name = "dba-sbx-acme-srv"
        self.ports = {"1433/tcp": [{"HostPort": "32768"}]}
        self.image = SimpleNamespace(tags=["mssql:2022"], short_id="abc")
        self.attrs = {"State": {"StartedAt": "2026-10-20T00:00:00Z"}}
        self.iniciado = self.eliminado = False

    def reload(self):
        pass

    def start(self):
        self.iniciado = True

    def remove(self, force):
        self.eliminado = True


class Docker:
    def __init__(self, existente=None):
        self.existente = existente
        self.creados = []
        self.volumen = SimpleNamespace(
            remove=lambda force: self.creados.append("volumen-eliminado")
        )
        self.containers = SimpleNamespace(get=self._get, run=self._run)
        self.volumes = SimpleNamespace(get=lambda nombre: self.volumen)

    def _get(self, nombre):
        if self.existente is None:
            raise NotFound("no")
        return self.existente

    def _run(self, imagen, **opciones):
        self.creados.append((imagen, opciones))
        self.existente = Contenedor()
        return self.existente


@pytest.fixture
def docker_falso(monkeypatch):
    falso = Docker()
    monkeypatch.setattr(contenedores, "_cliente", lambda: falso)
    monkeypatch.setattr(contenedores, "_esperar_motor", lambda destino: None)
    return falso


def test_asegurar_crea_el_contenedor_con_limites(docker_falso):
    destino = contenedores.asegurar("acme-srv")
    imagen, opciones = docker_falso.creados[0]
    assert imagen.startswith("mcr.microsoft.com/mssql/server:2022")
    assert opciones["mem_limit"] == "2g" and opciones["environment"]["MSSQL_PID"] == "Developer"
    assert opciones["ports"] == {"1433/tcp": ("127.0.0.1", None)}
    assert destino == contenedores.Destino("127.0.0.1,32768", "sa", "Sa#Prueba1")


def test_asegurar_arranca_uno_detenido_y_usa_la_red(docker_falso, monkeypatch):
    monkeypatch.setenv("SANDBOX_RED", "dba-assistant")
    config.configuracion.cache_clear()
    docker_falso.existente = Contenedor("exited")
    destino = contenedores.asegurar("acme-srv")
    assert docker_falso.existente.iniciado and destino.servidor == "dba-sbx-acme-srv,1433"


def test_estado_y_eliminar(docker_falso):
    assert contenedores.estado("acme-srv") == {"existe": False}
    docker_falso.existente = Contenedor()
    assert contenedores.estado("acme-srv")["imagen"] == "mssql:2022"
    contenedores.eliminar("acme-srv")
    assert docker_falso.existente.eliminado and "volumen-eliminado" in docker_falso.creados


def test_clave_invalida():
    for clave in ("", "ACME", "a b", "../x", "x" * 61):
        with pytest.raises(ValueError):
            contenedores.validar_clave(clave)


def test_cadenas_de_conexion():
    destino = contenedores.Destino("srv,1433", "sa", 'p;"x')
    assert 'PWD={p;"x}' in contenedores.cadena_conexion(destino, "ERP")
    assert contenedores.cadena_sqlclient(destino, "ERP") == (
        'Server=srv,1433;Database="ERP";User Id="sa";Password="p;""x";'
        "Encrypt=True;TrustServerCertificate=True"
    )


def test_esperar_motor_reintenta_y_falla(monkeypatch):
    intentos = []

    def conectar(*_, **__):
        intentos.append(1)
        raise pyodbc.OperationalError("08001", "no")

    monkeypatch.setenv("SANDBOX_ESPERA_INICIO_SEGUNDOS", "0")
    config.configuracion.cache_clear()
    monkeypatch.setattr(contenedores.pyodbc, "connect", conectar)
    with pytest.raises(pyodbc.Error):
        contenedores._esperar_motor(contenedores.Destino("x", "sa", "p"))
    assert intentos == [1]


# --- Estructura (sqlpackage) --------------------------------------------------------


def test_sincronizar_base_extrae_y_publica(monkeypatch):
    llamadas = []

    def ejecutar(comando, **_):
        llamadas.append(comando)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(estructura.subprocess, "run", ejecutar)
    origen = contenedores.Destino("origen,1433", "lector", "p")
    destino = contenedores.Destino("sbx,1433", "sa", "s")
    assert estructura.sincronizar_base(origen, "ERP", destino)["estado"] == "ok"
    extraer, publicar = llamadas
    assert extraer[:2] == ["dotnet", "/opt/sqlpackage/sqlpackage.dll"]
    assert "/Action:Extract" in extraer and "/p:ExtractAllTableData=false" in extraer
    assert any("Server=origen,1433" in a for a in extraer)
    assert "/p:CreateNewDatabase=true" in publicar and any("Server=sbx,1433" in a for a in publicar)


def test_sincronizar_base_informa_errores(monkeypatch):
    def fallar(comando, **_):
        return SimpleNamespace(returncode=1, stdout="linea\n*** Error: login failed", stderr="")

    monkeypatch.setattr(estructura.subprocess, "run", fallar)
    destino = contenedores.Destino("sbx", "sa", "s")
    resultado = estructura.sincronizar_base(destino, "ERP", destino)
    assert resultado["estado"] == "error" and "login failed" in resultado["error"]

    def demorar(comando, **_):
        raise subprocess.TimeoutExpired(comando, 1)

    monkeypatch.setattr(estructura.subprocess, "run", demorar)
    assert estructura.sincronizar_base(destino, "ERP", destino)["estado"] == "error"


def test_huella(monkeypatch):
    class Conexion:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self):
            return SimpleNamespace(
                execute=lambda q: SimpleNamespace(fetchone=lambda: (2, 1, 3, None))
            )

    monkeypatch.setattr(estructura.pyodbc, "connect", lambda *a, **k: Conexion())
    assert estructura.huella("cadena") == [2, 1, 3, None]


# --- Ejecución con rollback ---------------------------------------------------------

PLAN = (
    '<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan"><BatchSequence>'
    '<Batch><Statements><StmtSimple StatementSubTreeCost="0.5"><QueryPlan>'
    '<RelOp PhysicalOp="Index Seek" EstimateIO="0.25"/></QueryPlan></StmtSimple>'
    "</Statements></Batch></BatchSequence></ShowPlanXML>"
)


class Cursor:
    """Simula los conjuntos de resultados de un lote con SET STATISTICS XML ON."""

    def __init__(self, conexion):
        self.conexion = conexion
        self.conjuntos = []
        self.messages = []

    def execute(self, sql):
        self.conexion.ejecutados.append(sql)
        if sql in self.conexion.errores:
            raise self.conexion.errores[sql]
        self.conjuntos = list(self.conexion.respuestas.get(sql, [(None, 0, [])]))
        self._cargar()
        return self

    def _cargar(self):
        self.description, self.rowcount, self.datos = self.conjuntos.pop(0)

    def nextset(self):
        if not self.conjuntos:
            return False
        self._cargar()
        return True

    def fetchall(self):
        return self.datos

    def fetchmany(self, cantidad):
        return self.datos[:cantidad]


class ConexionSandbox:
    def __init__(self, respuestas=None, errores=None):
        self.respuestas = respuestas or {}
        self.errores = errores or {}
        self.ejecutados = []
        self.revertida = self.cerrada = False

    def cursor(self):
        return Cursor(self)

    def rollback(self):
        self.revertida = True

    def close(self):
        self.cerrada = True


def _plan_columnas():
    return [(ejecucion.COLUMNA_PLAN, None)]


def test_ejecutar_mide_y_revierte(monkeypatch):
    conexion = ConexionSandbox(
        respuestas={
            "UPDATE t": [(None, 3, []), (_plan_columnas(), -1, [(PLAN,)])],
            "SELECT * FROM t": [([("id", None), ("x", None)], -1, [(1, "a"), (2, None)])],
        }
    )
    monkeypatch.setattr(ejecucion.pyodbc, "connect", lambda *a, c=conexion, **k: c)
    resultado = ejecucion.ejecutar(
        contenedores.Destino("s", "sa", "p"), "ERP", ["UPDATE t", "SELECT * FROM t"], 60
    )
    assert resultado["estado"] == "ok" and resultado["filas"] == 3
    assert resultado["muestra"] == {"columnas": ["id", "x"], "filas": [["1", "a"], ["2", None]]}
    assert resultado["plan"] == {"operador": "Index Seek", "costo": 0.5, "costo_io": 0.25}
    assert conexion.revertida and conexion.cerrada
    assert conexion.ejecutados[:2] == ["SET NOEXEC ON", "UPDATE t"]


def test_ejecutar_informa_sintaxis_timeout_y_errores(monkeypatch):
    destino = contenedores.Destino("s", "sa", "p")
    casos = [
        ({"MAL": pyodbc.ProgrammingError("42000", "Incorrect syntax")}, ["MAL"], "sintaxis"),
    ]
    for errores, lotes, esperado in casos:
        conexion = ConexionSandbox(errores=errores)
        monkeypatch.setattr(ejecucion.pyodbc, "connect", lambda *a, c=conexion, **k: c)
        assert ejecucion.ejecutar(destino, "ERP", lotes, 60)["estado"] == esperado

    class ConexionQueFalla(ConexionSandbox):
        def cursor(self):
            cursor = Cursor(self)
            original = cursor.execute

            def execute(sql):
                if sql == "LENTO" and "SET STATISTICS XML ON" in self.ejecutados:
                    raise self.error
                return original(sql)

            cursor.execute = execute
            return cursor

    for error, esperado in (
        (pyodbc.OperationalError("HYT00", "Query timeout expired"), "timeout"),
        (pyodbc.OperationalError("08S01", "Communication link failure"), "error"),
        (pyodbc.ProgrammingError("42000", "Divide by zero"), "error"),
    ):
        conexion = ConexionQueFalla()
        conexion.error = error
        monkeypatch.setattr(ejecucion.pyodbc, "connect", lambda *a, c=conexion, **k: c)
        resultado = ejecucion.ejecutar(destino, "ERP", ["LENTO"], 1)
        assert resultado["estado"] == esperado and conexion.revertida

    conexion = ConexionSandbox()
    monkeypatch.setattr(ejecucion.pyodbc, "connect", lambda *a, c=conexion, **k: c)
    assert (
        ejecucion.ejecutar(destino, "ERP", ["SELECT 1"], 60, solo_sintaxis=True)["estado"] == "ok"
    )
    assert "SET STATISTICS XML ON" not in conexion.ejecutados


def test_resumen_de_plan_tolera_xml_invalido():
    assert ejecucion._resumen_plan(["<no", PLAN])["operador"] == "Index Seek"


# --- API ----------------------------------------------------------------------------


def test_api_exige_token_y_delega(monkeypatch):
    destino = contenedores.Destino("s", "sa", "p")
    monkeypatch.setattr(contenedores, "asegurar", lambda clave: destino)
    monkeypatch.setattr(contenedores, "estado", lambda clave: {"existe": True})
    monkeypatch.setattr(contenedores, "eliminar", lambda clave: None)
    monkeypatch.setattr(
        estructura, "sincronizar_base", lambda origen, base, d: {"base": base, "estado": "ok"}
    )
    huellas = iter([[1], [1], [1], [2]])
    monkeypatch.setattr(estructura, "huella", lambda cadena: next(huellas))
    monkeypatch.setattr(
        ejecucion, "ejecutar", lambda d, base, lotes, t, s: {"estado": "ok", "lotes": lotes}
    )
    cliente = TestClient(modulo_app.crear_app())
    origen = {"servidor": "o", "usuario": "u", "password": "p"}

    assert cliente.get("/sandboxes/acme").status_code == 401
    assert cliente.get("/sandboxes/acme", headers=TOKEN).json() == {"existe": True}
    assert cliente.get("/sandboxes/ACME", headers=TOKEN).status_code == 422
    assert cliente.post("/sandboxes/acme", headers=TOKEN).json() == {"existe": True}
    sincronizado = cliente.post(
        "/sandboxes/acme/sincronizar", json={"origen": origen, "bases": ["ERP"]}, headers=TOKEN
    )
    assert sincronizado.json() == {"bases": [{"base": "ERP", "estado": "ok"}]}
    alineadas = cliente.post(
        "/sandboxes/acme/huellas", json={"origen": origen, "bases": ["A", "B"]}, headers=TOKEN
    )
    assert alineadas.json() == {"alineadas": {"A": True, "B": False}}
    ejecutado = cliente.post(
        "/sandboxes/acme/ejecutar", json={"base": "ERP", "lotes": ["SELECT 1"]}, headers=TOKEN
    )
    assert ejecutado.json() == {"estado": "ok", "lotes": ["SELECT 1"]}
    assert cliente.delete("/sandboxes/acme", headers=TOKEN).status_code == 204
