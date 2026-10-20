import asyncio
import itertools
import time
from dataclasses import replace
from datetime import UTC, datetime
from datetime import time as hora
from types import SimpleNamespace

import pytest

from app.core.errores import ErrorDominio
from app.datos import winrm
from app.modulos.conexiones import servicios as conexiones
from app.modulos.workarounds import ejecutor, integrados, repositorios, servicios
from app.modulos.workarounds.entidades import Automatizacion, Paso, Propio
from tests.test_conexiones import (
    CABECERA,
    TENANT,
    _actor,
    _datos,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)

# --- Integrados: pasos a partir del diagnóstico ------------------------------------


def test_pasos_de_los_integrados():
    i = integrados.CATALOGO
    bloqueadores = {"bloqueadores": [{"sid": 87}, {"sid": 90}]}
    assert [
        p.sql for p in integrados.pasos(i["kill_bloqueos"], None, bloqueadores, {"sids": [87]})
    ] == ["KILL 87"]
    for sids in ([], [5], ["87"]):
        with pytest.raises(ErrorDominio) as error:
            integrados.pasos(i["kill_bloqueos"], None, bloqueadores, {"sids": sids})
        assert error.value.codigo == "WKR_EJE_001"

    log = {"archivo": "ERP_log", "usado_mb": 500}
    assert (
        integrados.pasos(i["shrink_log"], "ERP", log, {})[1].sql
        == "DBCC SHRINKFILE (N'ERP_log', 600)"
    )
    assert (
        "64)"
        in integrados.pasos(i["shrink_log"], "ERP", {"archivo": "x", "usado_mb": 1}, {})[1].sql
    )

    indices = {
        "indices": [{"esquema": "dbo", "tabla": "t]x", "indice": "IX", "fragmentacion": 80.0}],
        "online": True,
    }
    assert integrados.pasos(i["rebuild_indices"], "ERP", indices, {})[0].sql == (
        "ALTER INDEX [IX] ON [dbo].[t]]x] REBUILD WITH (ONLINE = ON)"
    )
    stats = {"estadisticas": [{"esquema": "dbo", "tabla": "t", "estadistica": "s"}]}
    assert integrados.pasos(i["update_statistics"], "ERP", stats, {})[0].sql == (
        "UPDATE STATISTICS [dbo].[t] [s]"
    )
    assert (
        "CLEAR PROCEDURE_CACHE" in integrados.pasos(i["limpiar_plan_cache"], "ERP", {}, {})[0].sql
    )
    assert integrados.pasos(i["checkpoint"], "ERP", {}, {})[0].sql == "CHECKPOINT"

    servicios_win = {"servicios": [{"nombre": "MSSQLSERVER", "estado": "Stopped"}]}
    paso = integrados.pasos(
        i["iniciar_servicio"], None, servicios_win, {"servicio": "MSSQLSERVER"}
    )[0]
    assert "Start-Service -Name 'MSSQLSERVER'" in paso.powershell
    for servicio in ("MSSQL$X'; rm", "OTRO"):
        with pytest.raises(ErrorDominio):
            integrados.pasos(i["iniciar_servicio"], None, servicios_win, {"servicio": servicio})


def test_diagnosticos_con_cursor_falso():
    class Cursor:
        def __init__(self, filas):
            self.filas = filas

        def execute(self, consulta):
            self.actual = self.filas[consulta]
            return self

        def fetchall(self):
            return self.actual

        def fetchone(self):
            return self.actual[0]

    filas = {
        integrados.CONSULTA_BLOQUEADORES: [(87, "app", "h", "p", "ERP", 2, 4200, 1, " UPDATE x ")],
        integrados.CONSULTA_LOG: [("FULL", "LOG_BACKUP", "ERP_log", 1024, 900.5, 87.94)],
        integrados.CONSULTA_FRAGMENTACION: [("dbo", "t", "IX", 75.123, 5000, 3)],
        integrados.CONSULTA_ESTADISTICAS: [("dbo", "t", "s", 100, 50, datetime(2026, 1, 1))],
        integrados.CONSULTA_PLAN_CACHE: [(12, 3)],
    }
    conexion = SimpleNamespace(cursor=lambda: Cursor(filas))
    i = integrados.CATALOGO
    assert integrados.diagnosticar(i["kill_bloqueos"], conexion)["bloqueadores"][0] == {
        "sid": 87,
        "login": "app",
        "host": "h",
        "programa": "p",
        "base": "ERP",
        "bloquea_a": 2,
        "espera_s": 4.2,
        "raiz": True,
        "consulta": "UPDATE x",
    }
    assert integrados.diagnosticar(i["shrink_log"], conexion)["usado_mb"] == 900
    rebuild = integrados.diagnosticar(i["rebuild_indices"], conexion)
    assert rebuild["online"] and rebuild["indices"][0]["fragmentacion"] == 75.1
    assert integrados.diagnosticar(i["update_statistics"], conexion)["estadisticas"][0][
        "actualizada"
    ]
    assert integrados.diagnosticar(i["limpiar_plan_cache"], conexion) == {
        "planes": 12,
        "tamano_mb": 3,
    }
    assert integrados.diagnosticar(i["checkpoint"], conexion) == {}
    assert integrados.servicios_desde_json('{"Name":"MSSQLSERVER","Status":"Stopped"}') == [
        {"nombre": "MSSQLSERVER", "estado": "Stopped"}
    ]
    assert integrados.servicios_desde_json("") == []


def test_sugerido_para_alertas():
    assert integrados.sugerido_para("evento:bloqueo") == ("kill_bloqueos", {})
    assert integrados.sugerido_para("evento:log:ERP") == ("shrink_log", {"base": "ERP"})
    assert integrados.sugerido_para("evento:instancia") == ("iniciar_servicio", {})
    assert integrados.sugerido_para("umbral:cpu") is None


# --- Ventana de mantenimiento -------------------------------------------------------


def test_dentro_de_ventana_con_zona_horaria_y_medianoche():
    from app.modulos.conexiones.entidades import Conexion

    base = Conexion("c", "t", "A", "h", None, None, "DEV", "u", "")
    # 01:30 UTC = 22:30 en Buenos Aires.
    a_las = datetime(2026, 10, 20, 1, 30, tzinfo=UTC)
    assert servicios.dentro_de_ventana(base, a_las) is False
    nocturna = replace(base, ventana_inicio=hora(22, 0), ventana_fin=hora(6, 0))
    assert servicios.dentro_de_ventana(nocturna, a_las) is True
    diurna = replace(base, ventana_inicio=hora(9, 0), ventana_fin=hora(18, 0))
    assert servicios.dentro_de_ventana(diurna, a_las) is False
    assert servicios.dentro_de_ventana(replace(diurna, zona_horaria="UTC"), a_las) is False


# --- Ejecutor -----------------------------------------------------------------------


class ConexionSql:
    def __init__(self, falla_en: str | None = None):
        self.falla_en = falla_en
        self.ejecutados = []
        self.commits = self.rollbacks = 0
        self.autocommit = True
        self.cerrada = False

    def cursor(self):
        return self

    def execute(self, sql):
        if sql == self.falla_en:
            raise RuntimeError("Divide by zero")
        self.ejecutados.append(sql)
        self.rowcount = 1
        self.messages = [("01000", "listo")]
        return self

    def nextset(self):
        return False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.cerrada = True


@pytest.fixture
def instancia(entorno, monkeypatch):  # noqa: F811
    estado = {"abiertas": [], "falla_en": None, "abrir_falla": False}

    def abrir(*_, **__):
        if estado["abrir_falla"]:
            raise RuntimeError("no responde")
        conexion = ConexionSql(estado["falla_en"])
        estado["abiertas"].append(conexion)
        return conexion

    monkeypatch.setattr(ejecutor.sqlserver, "abrir_externo", abrir)
    return estado


def _conexion_guardada(**cambios):
    from app.core import cifrado
    from app.modulos.conexiones.entidades import Conexion

    base = Conexion(
        "c1",
        str(TENANT.id),
        "ERP",
        "h",
        None,
        None,
        "DEV",
        "u",
        cifrado.cifrar("x"),
        winrm_usuario="PC\\u",
        winrm_password_cifrada=cifrado.cifrar("w"),
        winrm_puerto=5985,
    )
    return replace(base, **cambios)


def test_ejecutor_transaccional_revierte(instancia):
    instancia["falla_en"] = "SELECT 1/0"
    progresos = []
    estado, error = ejecutor.ejecutar(
        _conexion_guardada(),
        "ERP",
        [Paso("a", sql="UPDATE t"), Paso("b", sql="SELECT 1/0"), Paso("c", sql="X")],
        True,
        lambda pasos, e, err: progresos.append((e, [p["estado"] for p in pasos])),
    )
    assert estado == "revertida" and "Divide" in error
    assert progresos[-1] == ("revertida", ["revertido", "error", "omitido"])
    conexion = instancia["abiertas"][0]
    assert conexion.rollbacks == 1 and conexion.autocommit is False and conexion.cerrada


def test_ejecutor_no_transaccional_se_detiene(instancia):
    instancia["falla_en"] = "B"
    estado, _ = ejecutor.ejecutar(
        _conexion_guardada(),
        None,
        [Paso("a", sql="A"), Paso("b", sql="B")],
        False,
        lambda *_: None,
    )
    assert estado == "fallida" and instancia["abiertas"][0].rollbacks == 0


def test_ejecutor_exitoso_con_powershell(instancia, monkeypatch):
    monkeypatch.setattr(
        winrm, "ejecutar_powershell", lambda *a: winrm.ResultadoComando(0, "Running", "")
    )
    progresos = []
    estado, error = ejecutor.ejecutar(
        _conexion_guardada(),
        None,
        [Paso("s", powershell="Start-Service x")],
        False,
        lambda pasos, e, err: progresos.append(pasos[0]["mensaje"]),
    )
    assert (estado, error) == ("exitosa", None) and progresos[-1] == "Running"

    monkeypatch.setattr(
        winrm, "ejecutar_powershell", lambda *a: winrm.ResultadoComando(1, "", "Acceso denegado")
    )
    estado, error = ejecutor.ejecutar(
        _conexion_guardada(), None, [Paso("s", powershell="x")], False, lambda *_: None
    )
    assert estado == "fallida" and "Acceso denegado" in error
    estado, error = ejecutor.ejecutar(
        _conexion_guardada(winrm_usuario=None),
        None,
        [Paso("s", powershell="x")],
        False,
        lambda *_: None,
    )
    assert "WinRM" in error


def test_ejecutor_instancia_caida(instancia):
    instancia["abrir_falla"] = True
    estado, error = ejecutor.ejecutar(
        _conexion_guardada(), None, [Paso("a", sql="A")], True, lambda *_: None
    )
    assert (estado, error) == ("fallida", "no responde")


def test_ejecutor_exitoso_transaccional_confirma(instancia):
    estado, _ = ejecutor.ejecutar(
        _conexion_guardada(), "ERP", [Paso("a", sql="A")], True, lambda *_: None
    )
    assert estado == "exitosa" and instancia["abiertas"][0].commits == 1


# --- Servicios con repositorio en memoria -------------------------------------------


class RepositorioFalso:
    def __init__(self):
        self.propios: dict[str, Propio] = {}
        self.versiones: dict[tuple[str, int], tuple[str, ...]] = {}
        self.ejecuciones: dict[int, object] = {}
        self.autorizaciones: dict[str, SimpleNamespace] = {}
        self.automatizaciones: dict[str, Automatizacion] = {}
        self.ids = itertools.count(1)

    def instalar(self, monkeypatch):
        for nombre in (
            "listar_propios",
            "obtener_propio",
            "pasos_de_version",
            "listar_versiones",
            "existe_nombre",
            "guardar_propio",
            "eliminar_propio",
            "crear_ejecucion",
            "actualizar_ejecucion",
            "obtener_ejecucion",
            "listar_ejecuciones",
            "estadisticas",
            "cerrar_interrumpidas",
            "crear_autorizacion",
            "listar_autorizaciones",
            "obtener_autorizacion",
            "resolver_autorizacion",
            "consumir_autorizacion",
            "listar_automatizaciones",
            "crear_automatizacion",
            "eliminar_automatizacion",
        ):
            monkeypatch.setattr(repositorios, nombre, getattr(self, nombre))
        return self

    def _uuid(self):
        return f"00000000-0000-0000-0000-{next(self.ids):012d}"

    def listar_propios(self, base, tenant_id):
        return list(self.propios.values())

    def obtener_propio(self, base, tenant_id, workaround_id):
        return self.propios.get(workaround_id)

    def pasos_de_version(self, base, workaround_id, version):
        return self.versiones.get((workaround_id, version))

    def listar_versiones(self, base, workaround_id):
        return [
            SimpleNamespace(
                version=v,
                pasos=p,
                creada_por="dba",
                creada_en=datetime(2026, 1, 1),
                validada_en=None,
            )
            for (w, v), p in self.versiones.items()
            if w == workaround_id
        ]

    def existe_nombre(self, base, tenant_id, nombre, excluir_id):
        return any(w.nombre == nombre and w.id != excluir_id for w in self.propios.values())

    def guardar_propio(self, base, w, usuario):
        workaround_id = w.id or self._uuid()
        version = self.propios[w.id].version_actual + 1 if w.id else 1
        aprobada = self.propios[w.id].version_aprobada if w.id else None
        self.propios[workaround_id] = replace(
            w, id=workaround_id, version_actual=version, version_aprobada=aprobada
        )
        self.versiones[(workaround_id, version)] = w.pasos
        return workaround_id

    def eliminar_propio(self, base, tenant_id, workaround_id):
        self.propios.pop(workaround_id)

    def crear_ejecucion(self, base, tenant_id, e):
        ejecucion_id = next(self.ids)
        self.ejecuciones[ejecucion_id] = replace_ejecucion(e, ejecucion_id)
        return ejecucion_id

    def actualizar_ejecucion(self, base, ejecucion_id, pasos, estado, error):
        e = self.ejecuciones[ejecucion_id]
        e.pasos, e.estado, e.error = pasos, estado, error

    def obtener_ejecucion(self, base, tenant_id, ejecucion_id):
        return self.ejecuciones.get(ejecucion_id)

    def listar_ejecuciones(self, base, tenant_id, workaround=None, limite=50):
        return [e for e in self.ejecuciones.values() if workaround in (None, e.workaround)]

    def estadisticas(self, base, tenant_id):
        return {}

    def cerrar_interrumpidas(self, base, tenant_id):
        return 0

    def crear_autorizacion(self, base, tenant_id, workaround, conexion, motivo, usuario, horas):
        autorizacion_id = self._uuid()
        self.autorizaciones[autorizacion_id] = SimpleNamespace(
            id=autorizacion_id,
            workaround=workaround[0],
            workaround_nombre=workaround[1],
            conexion_id=conexion[0],
            conexion_alias=conexion[1],
            motivo=motivo,
            solicitada_por=usuario,
            solicitada_en=datetime(2026, 1, 1),
            estado="pendiente",
            resuelta_por=None,
            resuelta_en=None,
            vence_en=datetime(2099, 1, 1),
        )
        return autorizacion_id

    def listar_autorizaciones(self, base, tenant_id):
        return [a for a in self.autorizaciones.values() if a.estado in ("pendiente", "autorizada")]

    def obtener_autorizacion(self, base, tenant_id, autorizacion_id):
        return self.autorizaciones.get(autorizacion_id)

    def resolver_autorizacion(self, base, tenant_id, autorizacion_id, usuario, aprobar, horas):
        a = self.autorizaciones[autorizacion_id]
        if a.estado != "pendiente":
            return False
        a.estado, a.resuelta_por = ("autorizada" if aprobar else "rechazada"), usuario
        return True

    def consumir_autorizacion(self, base, tenant_id, workaround, conexion_id, usuario):
        for a in self.autorizaciones.values():
            if (a.workaround, a.conexion_id, a.solicitada_por, a.estado) == (
                workaround,
                conexion_id,
                usuario,
                "autorizada",
            ):
                a.estado = "usada"
                return a.resuelta_por
        return None

    def listar_automatizaciones(self, base, tenant_id):
        return list(self.automatizaciones.values())

    def crear_automatizacion(self, base, tenant_id, a):
        automatizacion_id = self._uuid()
        self.automatizaciones[automatizacion_id] = replace(a, id=automatizacion_id)
        return automatizacion_id

    def eliminar_automatizacion(self, base, tenant_id, automatizacion_id):
        return self.automatizaciones.pop(automatizacion_id, None) is not None


def replace_ejecucion(e, ejecucion_id):
    e.id = ejecucion_id
    e.iniciada_en = datetime(2026, 1, 1)
    return e


@pytest.fixture
def wk(entorno, monkeypatch):  # noqa: F811
    repo = RepositorioFalso().instalar(monkeypatch)
    avisos, transacciones, corridas = [], [], []

    async def avisar(tenant, roles, titulo, cuerpo, ruta):
        avisos.append((roles, titulo, cuerpo))

    async def registrar_transaccion(base, evento):
        transacciones.append(evento)

    def ejecutar(conexion, base, pasos, transaccional, progreso):
        corridas.append([p.sql or p.powershell for p in pasos])
        progreso([{"estado": "ok"}], "exitosa", None)
        return "exitosa", None

    diagnosticos = {"kill_bloqueos": {"bloqueadores": [{"sid": 87}]}, "checkpoint": {}}
    monkeypatch.setattr(servicios.notificaciones, "avisar", avisar)
    monkeypatch.setattr(servicios.auditoria, "registrar_transaccion", registrar_transaccion)
    monkeypatch.setattr(servicios.ejecutor, "ejecutar", ejecutar)
    monkeypatch.setattr(
        servicios, "_leer_diagnostico", lambda conexion, base, w: diagnosticos.get(w.id, {})
    )
    monkeypatch.setattr(servicios, "_validar_sintaxis", lambda *a: None)
    return {
        **entorno,
        "repo": repo,
        "avisos": avisos,
        "transacciones": transacciones,
        "corridas": corridas,
        "diagnosticos": diagnosticos,
    }


async def _crear_conexion(ambiente="DEV", **cambios):
    return await conexiones.crear(TENANT, _actor(), _datos(ambiente=ambiente, **cambios))


def _dba(wk, ambientes=("DEV", "TESTING", "PROD"), segundo_factor=None):
    usuario = wk["usuarios"].agregar(f"dba{time.time_ns()}@acme.com", "DBA", "activo")
    wk["usuarios"].usuarios[usuario.id] = replace(usuario, ambientes=ambientes)
    return replace(
        _actor(segundo_factor_en=segundo_factor), usuario_id=usuario.id, email=usuario.email
    )


async def _terminar():
    while servicios._tareas:
        await asyncio.sleep(0.01)


def _propio(**cambios):
    base = Propio(
        "",
        "",
        "Archivar",
        "Mueve filas",
        "mantenimiento",
        "info",
        ("DEV", "PROD"),
        ("DBA",),
        True,
        None,
        0,
        None,
        ("UPDATE t SET x = 1",),
    )
    return replace(base, **cambios)


async def test_biblioteca_detalle_y_propios(wk):
    conexion = await _crear_conexion()
    dba = _dba(wk)

    with pytest.raises(ErrorDominio) as error:
        await servicios.guardar_propio(
            TENANT, _actor("CONSULTA"), None, _propio(), (conexion.id, None)
        )
    assert error.value.codigo == "USR_PER_000"
    for cambios in ({"nombre": " "}, {"pasos": ()}, {"ambientes": ()}, {"categoria": "x"}):
        with pytest.raises(ErrorDominio) as error:
            await servicios.guardar_propio(
                TENANT, dba, None, _propio(**cambios), (conexion.id, None)
            )
        assert error.value.codigo == "USR_VAL_002"

    creado = await servicios.guardar_propio(TENANT, dba, None, _propio(), (conexion.id, "ERP"))
    assert creado["estado"] == "pendiente_sandbox" and creado["codigo"].startswith("WK-P")
    with pytest.raises(ErrorDominio) as error:
        await servicios.guardar_propio(TENANT, dba, None, _propio(), (conexion.id, None))
    assert error.value.codigo == "WKR_VAL_001"
    modificado = await servicios.guardar_propio(
        TENANT, dba, creado["id"], _propio(pasos=("UPDATE t SET x = 2",)), (conexion.id, None)
    )
    assert modificado["version_actual"] == 2 and len(modificado["versiones"]) == 2
    assert [e[0] for e in wk["eventos"][-2:]] == ["workaround.creado", "workaround.modificado"]

    biblioteca = await servicios.biblioteca(TENANT)
    assert len(biblioteca) == len(integrados.CATALOGO) + 1
    assert (await servicios.detalle(TENANT, "kill_bloqueos"))["versiones"] == []
    for workaround_id in ("no-uuid", "00000000-0000-0000-0000-000000009999"):
        with pytest.raises(ErrorDominio) as error:
            await servicios.detalle(TENANT, workaround_id)
        assert error.value.codigo == "WKR_VAL_000"

    await servicios.eliminar_propio(TENANT, dba, creado["id"])
    assert wk["eventos"][-1][0] == "workaround.eliminado"


async def test_ejecutar_controles_y_registro(wk):
    conexion = await _crear_conexion()
    dba = _dba(wk)

    diag = await servicios.diagnostico(TENANT, dba, "kill_bloqueos", conexion.id, None, {})
    assert diag["pasos"] == [] and diag["diagnostico"]["bloqueadores"][0]["sid"] == 87
    diag = await servicios.diagnostico(
        TENANT, dba, "kill_bloqueos", conexion.id, None, {"sids": [87]}
    )
    assert diag["pasos"][0]["comando"] == "KILL 87" and diag["prod"] is False

    ejecucion_id = await servicios.ejecutar(
        TENANT, dba, "kill_bloqueos", conexion.id, None, {"sids": [87]}, "mobile", 12
    )
    await _terminar()
    ejecucion = await servicios.obtener_ejecucion(TENANT, ejecucion_id)
    assert ejecucion["estado"] == "exitosa" and ejecucion["alerta_id"] == 12
    assert wk["corridas"][-1] == ["KILL 87"]
    assert wk["transacciones"][-1].detalle["mobile"] is True
    assert (await servicios.ejecuciones(TENANT))[0]["id"] == ejecucion_id

    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(TENANT, dba, "checkpoint", conexion.id, None, {}, "web")
    assert error.value.codigo == "USR_VAL_002"
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(
            TENANT, _actor("CONSULTA"), "kill_bloqueos", conexion.id, None, {}, "web"
        )
    assert error.value.codigo == "USR_PER_000"
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(
            TENANT, _dba(wk, ("PROD",)), "checkpoint", conexion.id, "ERP", {}, "web"
        )
    assert error.value.codigo == "USR_PER_000"
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(TENANT, dba, "checkpoint", conexion.id, "ERP", {}, "otro")
    assert error.value.codigo == "USR_VAL_002"
    with pytest.raises(ErrorDominio) as error:
        await servicios.obtener_ejecucion(TENANT, 999)
    assert error.value.codigo == "WKR_VAL_000"


async def test_ejecucion_fallida_avisa(wk, monkeypatch):
    conexion = await _crear_conexion()
    monkeypatch.setattr(servicios.ejecutor, "ejecutar", lambda *a: ("fallida", "Login failed"))
    await servicios.ejecutar(TENANT, _dba(wk), "checkpoint", conexion.id, "ERP", {}, "web")
    await _terminar()
    roles, _, cuerpo = wk["avisos"][-1]
    assert roles == ("ADMINISTRADOR", "DBA") and "Login failed" in cuerpo


async def test_propios_en_prod_solo_version_aprobada(wk):
    dev = await _crear_conexion()
    prod = await _crear_conexion("PROD", alias="ERP-PROD-02")
    dba = _dba(wk, segundo_factor=time.time())
    creado = await servicios.guardar_propio(TENANT, dba, None, _propio(), (dev.id, None))

    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(TENANT, dba, creado["id"], prod.id, "ERP", {}, "web")
    assert error.value.codigo == "WKR_SBX_001"

    # Versión 1 aprobada y versión 2 pendiente: en PROD se ejecuta la 1.
    repo = wk["repo"]
    repo.propios[creado["id"]] = replace(repo.propios[creado["id"]], version_aprobada=1)
    await servicios.guardar_propio(
        TENANT, dba, creado["id"], _propio(pasos=("UPDATE t SET x = 2",)), (dev.id, None)
    )
    diag = await servicios.diagnostico(TENANT, dba, creado["id"], prod.id, "ERP", {})
    assert diag["diagnostico"]["version"] == 1 and diag["prod"]
    await servicios.ejecutar(TENANT, dba, creado["id"], prod.id, "ERP", {}, "web")
    await servicios.ejecutar(TENANT, dba, creado["id"], dev.id, "ERP", {}, "web")
    await _terminar()
    assert wk["corridas"][-2:] == [["UPDATE t SET x = 1"], ["UPDATE t SET x = 2"]]

    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(TENANT, _dba(wk), creado["id"], prod.id, "ERP", {}, "web")
    assert error.value.codigo == "AMB_VAL_000"


async def test_ventana_y_autorizaciones(wk):
    conexion = await _crear_conexion()
    dba, otro = _dba(wk), _dba(wk)
    wk["diagnosticos"]["update_statistics"] = {
        "estadisticas": [{"esquema": "dbo", "tabla": "t", "estadistica": "s"}]
    }

    diag = await servicios.diagnostico(TENANT, dba, "update_statistics", conexion.id, "ERP", {})
    assert diag["requiere_ventana"] and not diag["dentro_de_ventana"] and diag["ventana"] is None
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(TENANT, dba, "update_statistics", conexion.id, "ERP", {}, "web")
    assert error.value.codigo == "WKR_VEN_000"

    with pytest.raises(ErrorDominio):
        await servicios.solicitar_autorizacion(TENANT, dba, "update_statistics", conexion.id, " ")
    with pytest.raises(ErrorDominio):
        await servicios.solicitar_autorizacion(TENANT, dba, "checkpoint", conexion.id, "x")
    await servicios.solicitar_autorizacion(TENANT, dba, "update_statistics", conexion.id, "Urgente")
    assert "Urgente" in wk["avisos"][-1][2]
    [pedido] = await servicios.autorizaciones(TENANT)

    for actor, autorizacion_id, codigo in (
        (dba, pedido["id"], "WKR_AUT_001"),
        (otro, "no-uuid", "WKR_AUT_000"),
        (otro, "00000000-0000-0000-0000-000000009999", "WKR_AUT_000"),
        (_actor("CONSULTA"), pedido["id"], "USR_PER_000"),
    ):
        with pytest.raises(ErrorDominio) as error:
            await servicios.resolver_autorizacion(TENANT, actor, autorizacion_id, True)
        assert error.value.codigo == codigo
    await servicios.resolver_autorizacion(TENANT, otro, pedido["id"], True)
    with pytest.raises(ErrorDominio):
        await servicios.resolver_autorizacion(TENANT, otro, pedido["id"], True)

    await servicios.ejecutar(TENANT, dba, "update_statistics", conexion.id, "ERP", {}, "web")
    await _terminar()
    ejecucion = (await servicios.ejecuciones(TENANT))[-1]
    assert ejecucion["autorizada_por"] == otro.email


async def test_automatizaciones_y_automatizador(wk, monkeypatch):
    conexion = await _crear_conexion(instancia="ERP")
    dba = _dba(wk)

    with pytest.raises(ErrorDominio) as error:
        await servicios.crear_automatizacion(TENANT, dba, "log", "kill_bloqueos", None)
    assert error.value.codigo == "WKR_AUT_002"
    with pytest.raises(ErrorDominio) as error:
        await servicios.crear_automatizacion(TENANT, dba, "instancia", "iniciar_servicio", None)
    assert error.value.codigo == "AMB_VAL_000"
    creada = await servicios.crear_automatizacion(
        TENANT, dba, "instancia", "iniciar_servicio", conexion.id
    )
    await servicios.crear_automatizacion(TENANT, dba, "log", "checkpoint", conexion.id)
    assert len(await servicios.automatizaciones(TENANT)) == 2

    wk["diagnosticos"]["iniciar_servicio"] = {
        "servicios": [{"nombre": "MSSQL$ERP", "estado": "Stopped"}]
    }
    automatizador = servicios.Automatizador()
    guardada = await conexiones.obtener(TENANT, conexion.id)
    await automatizador.alerta_disparada(
        TENANT, guardada, SimpleNamespace(clave="evento:instancia", id=5)
    )
    await automatizador.alerta_disparada(
        TENANT, guardada, SimpleNamespace(clave="evento:log:ERP", id=6)
    )
    await automatizador.alerta_disparada(
        TENANT, guardada, SimpleNamespace(clave="umbral:cpu", id=7)
    )
    await _terminar()
    automaticas = await servicios.ejecuciones(TENANT)
    assert [e["origen"] for e in automaticas] == ["automatica", "automatica"]
    assert wk["corridas"][0][0].startswith("Start-Service -Name 'MSSQL$ERP'")
    assert (
        automaticas[1]["base"] == "ERP"
        and automaticas[0]["ejecutada_por"] == servicios.USUARIO_AUTOMATICO
    )

    # Si no puede ejecutarse, se avisa al equipo DBA.
    wk["diagnosticos"]["iniciar_servicio"] = {"servicios": []}
    await automatizador.alerta_disparada(
        TENANT, guardada, SimpleNamespace(clave="evento:instancia", id=8)
    )
    assert "A-8" in wk["avisos"][-1][2]

    await servicios.eliminar_automatizacion(TENANT, dba, creada["id"])
    for automatizacion_id in ("no-uuid", creada["id"]):
        with pytest.raises(ErrorDominio):
            await servicios.eliminar_automatizacion(TENANT, dba, automatizacion_id)


async def test_api_workarounds(wk, monkeypatch):
    conexion = await _crear_conexion()
    navegador = await _navegador(wk, "DBA")
    sesion_usuario = list(wk["usuarios"].usuarios.values())[-1]
    wk["usuarios"].usuarios[sesion_usuario.id] = replace(sesion_usuario, ambientes=("DEV",))
    monkeypatch.setattr(servicios, "bases", lambda tenant, conexion_id: _lista(["ERP"]))

    biblioteca = navegador.get("/api/workarounds").json()
    assert biblioteca["permisos"]["crear"] is True and len(biblioteca["workarounds"]) == 7
    assert navegador.get("/api/workarounds/kill_bloqueos").json()["codigo"] == "WK-01"
    assert navegador.get(f"/api/workarounds/conexiones/{conexion.id}/bases").json() == {
        "bases": ["ERP"]
    }
    cuerpo = {"conexion_id": conexion.id, "base": "ERP", "parametros": {}}
    assert navegador.post(
        "/api/workarounds/checkpoint/diagnostico", json=cuerpo, headers=CABECERA
    ).json()["pasos"]
    ejecucion = navegador.post(
        "/api/workarounds/checkpoint/ejecutar", json=cuerpo, headers=CABECERA
    ).json()
    # La ejecución sigue en segundo plano en el loop del cliente de pruebas: acá solo
    # importa que quedó registrada (el resultado se prueba en los tests de servicios).
    estado = navegador.get(f"/api/workarounds/ejecuciones/{ejecucion['ejecucion_id']}").json()
    assert estado["estado"] in ("en_curso", "exitosa")
    assert navegador.get("/api/workarounds/ejecuciones").json()["ejecuciones"]

    propio = {
        "nombre": "Archivar",
        "descripcion": "x",
        "categoria": "mantenimiento",
        "criticidad": "info",
        "ambientes": ["DEV"],
        "roles": ["DBA"],
        "pasos": ["SELECT 1"],
        "validar_conexion_id": conexion.id,
    }
    creado = navegador.post("/api/workarounds", json=propio, headers=CABECERA).json()
    actualizado = navegador.put(
        f"/api/workarounds/{creado['id']}", json=propio, headers=CABECERA
    ).json()
    assert actualizado["version_actual"] == 2
    assert navegador.delete(f"/api/workarounds/{creado['id']}", headers=CABECERA).status_code == 204

    assert (
        navegador.post(
            "/api/workarounds/update_statistics/autorizaciones",
            json={"conexion_id": conexion.id, "motivo": "Urgente"},
            headers=CABECERA,
        ).status_code
        == 204
    )
    [pedido] = navegador.get("/api/workarounds/autorizaciones").json()["autorizaciones"]
    assert (
        navegador.post(
            f"/api/workarounds/autorizaciones/{pedido['id']}",
            json={"aprobar": True},
            headers=CABECERA,
        ).json()["codigo"]
        == "WKR_AUT_001"
    )

    automatizacion = navegador.post(
        "/api/workarounds/automatizaciones",
        json={"evento": "log", "workaround": "shrink_log", "conexion_id": conexion.id},
        headers=CABECERA,
    )
    assert automatizacion.status_code == 201
    assert len(navegador.get("/api/workarounds/automatizaciones").json()["automatizaciones"]) == 1
    assert (
        navegador.delete(
            f"/api/workarounds/automatizaciones/{automatizacion.json()['id']}", headers=CABECERA
        ).status_code
        == 204
    )
    assert navegador.get("/workarounds").status_code == 200

    consulta = await _navegador(wk, "CONSULTA")
    assert consulta.get("/api/workarounds").json()["codigo"] == "USR_PER_000"


async def _lista(valor):
    return valor


async def test_validar_en_sandbox_aprueba_o_conserva_la_anterior(wk, monkeypatch):
    conexion = await _crear_conexion()
    dba = _dba(wk)
    creado = await servicios.guardar_propio(
        TENANT, dba, None, _propio(pasos=("UPDATE a", "UPDATE b")), (conexion.id, None)
    )
    aprobadas = []
    resultados = iter(
        [
            {"id": "r1", "estado": "ok", "duracion_ms": 3, "filas": 0, "avisos": []},
            {"id": "r2", "estado": "error", "error": "Invalid object name", "avisos": []},
        ]
    )
    enviados = []

    async def ejecutar(tenant, actor, conexion_id, base, sql, solo_sintaxis):
        enviados.append(sql)
        return next(resultados)

    def aprobar(base, tenant_id, workaround_id, version, validacion):
        aprobadas.append(version)
        repo = wk["repo"]
        repo.propios[workaround_id] = replace(repo.propios[workaround_id], version_aprobada=version)

    monkeypatch.setattr(servicios.sandbox, "ejecutar", ejecutar)
    monkeypatch.setattr(servicios.repositorios, "aprobar_version", aprobar)

    with pytest.raises(ErrorDominio) as error:
        await servicios.validar_en_sandbox(
            TENANT, _actor("CONSULTA"), creado["id"], conexion.id, "ERP"
        )
    assert error.value.codigo == "USR_PER_000"

    validado = await servicios.validar_en_sandbox(TENANT, dba, creado["id"], conexion.id, "ERP")
    assert enviados[0] == "UPDATE a\nGO\nUPDATE b"
    assert validado["estado"] == "aprobado" and validado["validacion"]["registro_sandbox"] == "r1"
    assert wk["eventos"][-1][0] == "workaround.validado"

    await servicios.guardar_propio(
        TENANT, dba, creado["id"], _propio(pasos=("UPDATE c",)), (conexion.id, None)
    )
    with pytest.raises(ErrorDominio) as error:
        await servicios.validar_en_sandbox(TENANT, dba, creado["id"], conexion.id, "ERP")
    assert error.value.codigo == "WKR_SBX_000"
    assert aprobadas == [1] and wk["repo"].propios[creado["id"]].version_aprobada == 1
    assert wk["eventos"][-1][0] == "workaround.validacion_fallida"
