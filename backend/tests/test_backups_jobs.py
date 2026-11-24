"""Backups, jobs del SQL Server Agent y sus workarounds (WK-09 a WK-13): estado frente a la
política, alertas, lectura desde msdb y pasos de cada workaround."""

from dataclasses import replace
from datetime import UTC, datetime
from datetime import time as hora
from types import SimpleNamespace

import pyodbc
import pytest

from app.core.errores import ErrorDominio
from app.modulos.alertas import condiciones
from app.modulos.monitor import lectura, repositorios, servicios
from app.modulos.monitor.entidades import (
    Lectura,
    PoliticaBackup,
    agente_detenido,
    estado_backup,
    estado_job,
    falla_vigente,
)
from app.modulos.workarounds import integrados
from app.modulos.workarounds import servicios as workarounds
from tests.test_alertas import _conexion as _conexion_alertas
from tests.test_alertas import alertas  # noqa: F401 (fixture)
from tests.test_conexiones import CABECERA, TENANT, _actor, _navegador
from tests.test_monitor import (  # noqa: F401 (fixtures)
    ConexionFalsa,
    _crear,
    _respuestas,
    entorno,
    monitor,
)

POLITICA = PoliticaBackup()  # full cada 24 h, log cada 60 min


def _base(**cambios) -> dict:
    datos = {
        "base": "ERP",
        "recuperacion": "FULL",
        "creada_min": 100_000,
        "full_min": 60,
        "diferencial_min": None,
        "log_min": 10,
    }
    return {**datos, **cambios}


def _job(**cambios) -> dict:
    datos = {
        "id": "0AB5C67A-7114-4B59-B03A-65BDFEA1FE62",
        "nombre": "Cierre diario",
        "habilitado": True,
        "resultado": "exito",
        "hace_min": 30,
        "duracion_s": 40,
        "error": None,
        "corriendo_s": None,
        "promedio_s": 60,
        "proxima": "2026-11-17T02:00",
    }
    return {**datos, **cambios}


# --- Estado frente a la política ----------------------------------------------------


def test_estado_backup_frente_a_la_politica():
    assert estado_backup(_base(), POLITICA) == {"full": "ok", "log": "ok"}
    assert estado_backup(_base(full_min=25 * 60), POLITICA)["full"] == "warning"
    assert estado_backup(_base(full_min=49 * 60), POLITICA)["full"] == "critical"
    # Sin backup full: crítico, salvo una base recién creada.
    assert estado_backup(_base(full_min=None), POLITICA) == {"full": "critical", "log": None}
    assert estado_backup(_base(full_min=None, creada_min=30), POLITICA)["full"] == "ok"
    # Sin backups de log, la cadena se cuenta desde el último full.
    assert estado_backup(_base(log_min=None, full_min=30), POLITICA)["log"] == "ok"
    assert estado_backup(_base(log_min=None, full_min=90), POLITICA)["log"] == "warning"
    assert estado_backup(_base(log_min=61), POLITICA)["log"] == "warning"
    # En recuperación SIMPLE el log no aplica; con la política apagada, nada aplica.
    assert estado_backup(_base(recuperacion="SIMPLE"), POLITICA)["log"] is None
    apagada = PoliticaBackup(habilitada=False)
    assert estado_backup(_base(full_min=None), apagada) == {"full": None, "log": None}


def test_estado_de_jobs_y_agente():
    assert estado_job(_job()) == "ok"
    assert estado_job(_job(habilitado=False, resultado="falla")) == "deshabilitado"
    assert estado_job(_job(resultado=None)) == "sin_ejecuciones"
    assert estado_job(_job(resultado="falla")) == "falla"
    assert estado_job(_job(corriendo_s=100)) == "en_curso"
    # Colgado: más de 3 veces su promedio y al menos 5 minutos.
    assert estado_job(_job(corriendo_s=301, promedio_s=60)) == "colgado"
    assert estado_job(_job(corriendo_s=250, promedio_s=60)) == "en_curso"
    assert estado_job(_job(corriendo_s=9000, promedio_s=None)) == "en_curso"
    assert falla_vigente(_job(resultado="falla", hace_min=60))
    assert not falla_vigente(_job(resultado="falla", hace_min=8 * 24 * 60))
    assert agente_detenido({"estado": "Stopped", "inicio": "Automatic"})
    assert not agente_detenido({"estado": "Stopped", "inicio": "Disabled"})
    assert not agente_detenido({"estado": "Running", "inicio": "Automatic"})
    assert not agente_detenido(None)


# --- Alertas ------------------------------------------------------------------------


def test_condiciones_de_backups():
    lectura_ = Lectura(
        valores={},
        backups=[
            _base(base="Ventas", full_min=30 * 60),
            _base(base="Compras", full_min=None),
            _base(base="Stock", log_min=200),
            _base(base="Ok"),
        ],
    )
    por_clave = {c.clave: c for c in condiciones.por_backups(lectura_, POLITICA)}
    assert set(por_clave) == {
        "evento:backup_full:Ventas",
        "evento:backup_full:Compras",
        "evento:backup_log:Stock",
    }
    ventas = por_clave["evento:backup_full:Ventas"]
    assert ventas.severidad == "warning" and ventas.push and ventas.email
    assert "30 h" in ventas.descripcion and "24 h" in ventas.descripcion
    assert por_clave["evento:backup_full:Compras"].severidad == "critical"
    assert "40" in por_clave["evento:backup_full:Compras"].descripcion
    assert "3 h 20 min" in por_clave["evento:backup_log:Stock"].descripcion
    # Sin lectura de backups no hay condiciones.
    assert condiciones.por_backups(Lectura(valores={}), POLITICA) == []


def test_condiciones_de_jobs_y_agente():
    lectura_ = Lectura(
        valores={},
        agente={"estado": "Stopped", "inicio": "Automatic"},
        jobs=[
            _job(resultado="falla", error="Paso 1: división por cero"),
            _job(id="B", nombre="Viejo", resultado="falla", hace_min=9 * 24 * 60),
            _job(id="C", nombre="Lento", corriendo_s=4000, promedio_s=600),
        ],
    )
    por_clave = {c.clave: c for c in condiciones.por_agente(lectura_, "sql01")}
    assert set(por_clave) == {
        "evento:agente",
        f"evento:job:{_job()['id']}",
        "evento:job_colgado:C",
    }
    assert por_clave["evento:agente"].severidad == "critical"
    assert "sql01" in por_clave["evento:agente"].descripcion
    falla = por_clave[f"evento:job:{_job()['id']}"]
    assert falla.push and "división por cero" in falla.descripcion
    colgado = por_clave["evento:job_colgado:C"]
    assert colgado.push and not colgado.email and "10 min" in colgado.descripcion
    assert condiciones._duracion(3 * 24 * 60 + 60) == "3 d 1 h"
    assert condiciones._duracion(2 * 24 * 60) == "2 d"
    assert condiciones._duracion(120) == "2 h"


async def test_motor_no_normaliza_lo_que_no_pudo_evaluar(alertas):  # noqa: F811
    """Sin permiso sobre msdb, las alertas de backups siguen activas."""
    from app.modulos.alertas import motor

    conexion = await _conexion_alertas()
    m = motor.Motor()
    repo = alertas["alertas"]
    sin_backup = Lectura(valores={}, backups=[_base(full_min=None)], jobs=[], agente=None)
    await m.evaluar(TENANT, conexion, sin_backup)
    [alerta] = repo.alertas.values()
    assert alerta.clave == "evento:backup_full:ERP" and alerta.severidad == "critical"
    sin_permiso = Lectura(valores={}, no_evaluadas=(lectura.PREFIJO_BACKUPS,))
    await m.evaluar(TENANT, conexion, sin_permiso)
    assert not repo.alertas[alerta.id].normalizada
    await m.evaluar(TENANT, conexion, Lectura(valores={}, backups=[_base()]))
    assert repo.alertas[alerta.id].normalizada


# --- Lectura desde msdb -------------------------------------------------------------


def test_lectura_de_backups_jobs_y_agente_con_su_frecuencia():
    estado = lectura.EstadoLectura()
    conexion = ConexionFalsa(_respuestas())
    primera = lectura.leer(conexion, estado, 1000.0)
    assert primera.backups == [
        {
            "base": "ERP",
            "recuperacion": "FULL",
            "creada_min": 90_000,
            "full_min": 1500,
            "diferencial_min": None,
            "log_min": None,
        }
    ]
    assert primera.agente == {"estado": "Running", "inicio": "Automatic"}
    assert primera.jobs[0]["resultado"] == "falla" and primera.jobs[0]["error"] == "Paso 1: falla"
    assert primera.no_evaluadas == ()
    # Los jobs se releen cada minuto y los backups cada 5: entretanto se reusa lo leído.
    segunda = lectura.leer(conexion, estado, 1070.0)
    ejecutadas = conexion.cursor_falso.ejecutadas
    assert ejecutadas.count(lectura.CONSULTA_BACKUPS) == 1
    assert ejecutadas.count(lectura.CONSULTA_JOBS) == 2
    assert segunda.backups == primera.backups


def test_lectura_sin_permiso_sobre_msdb():
    class SinPermiso(ConexionFalsa):
        def __init__(self):
            super().__init__(_respuestas())
            original = self.cursor_falso.execute

            def execute(consulta, *parametros):
                if "msdb" in consulta:
                    raise pyodbc.ProgrammingError("permiso denegado")
                return original(consulta, *parametros)

            self.cursor_falso.execute = execute

    resultado = lectura.leer(SinPermiso(), lectura.EstadoLectura(), 1.0)
    assert resultado.backups is None and resultado.jobs is None
    assert resultado.no_evaluadas == (lectura.PREFIJO_BACKUPS, *lectura.PREFIJOS_AGENTE)
    assert resultado.valores["cpu"] == 42.0


def test_historiales_de_backups_y_jobs():
    conexion = ConexionFalsa(
        {
            lectura.CONSULTA_HISTORIAL_BACKUPS: [
                ("D", 1, "2026-11-16T02:00:00", 12, 104857600, 52428800, "/b/a.bak", "sa"),
                ("L", 0, "2026-11-16T01:00:00", 1, 1048576, None, "/b/a.trn", "sa"),
            ],
            lectura.CONSULTA_HISTORIAL_JOB: [
                (0, "(Job outcome)", 0, "2026-11-16T03:00:00", 5, "The job failed."),
                (2, "Copiar", 0, "2026-11-16T03:00:04", 1, "Acceso denegado"),
                (1, "Preparar", 1, "2026-11-16T03:00:00", 4, "ok"),
                (0, "(Job outcome)", 1, "2026-11-15T03:00:00", 3, "The job succeeded."),
                (1, "Preparar", 1, "2026-11-15T03:00:00", 3, "ok"),
            ],
        }
    )
    backups = lectura.historial_backups(conexion, "ERP")
    assert backups[0] == {
        "tipo": "full",
        "copy_only": True,
        "fin": "2026-11-16T02:00:00",
        "duracion_s": 12,
        "tamano_mb": 100.0,
        "comprimido_mb": 50.0,
        "archivo": "/b/a.bak",
        "usuario": "sa",
    }
    assert backups[1]["tipo"] == "log" and backups[1]["comprimido_mb"] is None
    ejecuciones = lectura.historial_job(conexion, "x")
    assert [e["resultado"] for e in ejecuciones] == ["falla", "exito"]
    assert [p["nombre"] for p in ejecuciones[0]["pasos"]] == ["Preparar", "Copiar"]
    assert ejecuciones[0]["pasos"][1]["mensaje"] == "Acceso denegado"
    assert len(lectura.historial_job(conexion, "x", maximo=1)) == 1


# --- Workarounds WK-09 a WK-13 ------------------------------------------------------


class _Cursor:
    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.actual = []

    def execute(self, consulta, *parametros):
        self.actual = next(v for k, v in self.respuestas.items() if k in consulta)
        return self

    def fetchall(self):
        return self.actual

    def fetchone(self):
        return self.actual[0]


def _diagnostico_backup(**cambios) -> dict:
    datos = {
        "base": "ERP",
        "recuperacion": "FULL",
        "agente": "Running",
        "compresion": True,
        "carpeta": "/var/opt/mssql/data",
        "ahora_servidor": "2026-11-16T18:00",
        "desfase_min": -180,
        "cadena_log": True,
        "programados": [],
    }
    return {**datos, **cambios}


def test_diagnostico_de_backup_programado():
    respuestas = {
        "DB_NAME()": [("Ventas 2026",)],
        "EngineEdition": [
            ("FULL", 2, "Web Edition", "D:\\Backups", "2026-11-16T18:00", -180, "Running", 1)
        ],
        "sysjobs": [("DBA Assistant · Backup full · Ventas 2026 · x", "2026-11-17T02:00")],
    }
    conexion = SimpleNamespace(cursor=lambda: _Cursor(respuestas))
    datos = integrados.diagnosticar(integrados.CATALOGO["programar_backup_full"], conexion)
    assert datos["base"] == "Ventas 2026" and datos["agente"] == "Running"
    assert datos["compresion"] is False  # la edición Web no comprime
    assert datos["cadena_log"] and datos["programados"][0]["proxima"] == "2026-11-17T02:00"
    # Express no tiene Agent.
    respuestas["EngineEdition"] = [
        ("SIMPLE", 4, "Express Edition", None, "2026-11-16T18:00", 0, "Stopped", 0)
    ]
    datos = integrados.diagnosticar(integrados.CATALOGO["programar_backup_log"], conexion)
    assert datos["agente"] is None and datos["compresion"] is False
    assert integrados._patron_like("a_b%[c]\\") == "a\\_b\\%\\[c]\\\\"


def test_pasos_de_backup_programado():
    full = integrados.CATALOGO["programar_backup_full"]
    log = integrados.CATALOGO["programar_backup_log"]
    paso = integrados.pasos(
        full, "Ventas: 2026", _diagnostico_backup(), {"inicio": "2026-11-17T02:00"}
    )[0]
    assert "@delete_level = 1" in paso.sql and "@freq_type = 1" in paso.sql
    assert "@active_start_date = 20261117, @active_start_time = 020000" in paso.sql
    assert "BACKUP DATABASE [Ventas: 2026]" in paso.sql and "COPY_ONLY" in paso.sql
    assert "Ventas_2026_FULL_COPY_20261117_0200.bak" in paso.sql and "COMPRESSION" in paso.sql
    assert paso.descripcion.startswith("DBA Assistant · Backup full · Ventas: 2026 · 2026-11-17")
    paso_log = integrados.pasos(
        log, "ERP", _diagnostico_backup(compresion=False), {"inicio": "2026-11-17T02:00"}
    )[0]
    assert "BACKUP LOG [ERP]" in paso_log.sql and "COPY_ONLY" not in paso_log.sql
    assert "COMPRESSION" not in paso_log.sql and ".trn" in paso_log.sql

    casos = (
        (full, _diagnostico_backup(agente=None), "2026-11-17T02:00", "WKR_EJE_003"),
        (full, _diagnostico_backup(), "mañana", "WKR_EJE_004"),
        (full, _diagnostico_backup(), "2026-11-16T17:59", "WKR_EJE_004"),
        (full, _diagnostico_backup(), "2026-12-30T02:00", "WKR_EJE_004"),
        (log, _diagnostico_backup(recuperacion="SIMPLE"), "2026-11-17T02:00", "WKR_EJE_005"),
        (log, _diagnostico_backup(cadena_log=False), "2026-11-17T02:00", "WKR_EJE_005"),
    )
    for integrado, diagnostico, inicio, codigo in casos:
        with pytest.raises(ErrorDominio) as error:
            integrados.pasos(integrado, "ERP", diagnostico, {"inicio": inicio})
        assert error.value.codigo == codigo


def test_reintentar_job_y_query_store():
    reintentar = integrados.CATALOGO["reintentar_job"]
    respuestas = {lectura.CONSULTA_JOBS: [tuple(_respuestas()[lectura.CONSULTA_JOBS][0])]}
    conexion = SimpleNamespace(cursor=lambda: _Cursor(respuestas))
    diagnostico = integrados.diagnosticar(reintentar, conexion)
    assert diagnostico["jobs"][0]["estado"] == "falla"
    job_id = diagnostico["jobs"][0]["id"]
    [paso] = integrados.pasos(reintentar, None, diagnostico, {"job_id": job_id.lower()})
    assert paso.sql == f"EXEC msdb.dbo.sp_start_job @job_id = '{job_id}'"
    for elegido in ("'; DROP TABLE x --", "00000000-0000-0000-0000-000000000000", ""):
        with pytest.raises(ErrorDominio):
            integrados.pasos(reintentar, None, diagnostico, {"job_id": elegido})

    activar = integrados.CATALOGO["activar_query_store"]
    consulta = SimpleNamespace(
        cursor=lambda: _Cursor(
            {
                "ProductMajorVersion": [(16,)],
                "query_store_options": [("OFF", "OFF", 0, 0, 100, "AUTO")],
            }
        )
    )
    diagnostico = integrados.diagnosticar(activar, consulta)
    assert diagnostico["estado"] == "OFF" and diagnostico["maximo_mb"] == 100
    [paso] = integrados.pasos(activar, "ERP", diagnostico, {})
    assert "ALTER DATABASE [ERP] SET QUERY_STORE = ON" in paso.sql
    assert "MAX_STORAGE_SIZE_MB = 1024" in paso.sql
    # En solo lectura por falta de espacio, se le da el doble.
    lleno = {**diagnostico, "estado": "READ_ONLY", "usado_mb": 1000, "maximo_mb": 1024}
    assert "MAX_STORAGE_SIZE_MB = 2048" in integrados.pasos(activar, "ERP", lleno, {})[0].sql
    activo = {**diagnostico, "estado": "READ_WRITE", "deseado": "READ_WRITE"}
    with pytest.raises(ErrorDominio) as error:
        integrados.pasos(activar, "ERP", activo, {})
    assert error.value.codigo == "WKR_EJE_002"
    viejo = SimpleNamespace(cursor=lambda: _Cursor({"ProductMajorVersion": [(12,)]}))
    diagnostico = integrados.diagnosticar(activar, viejo)
    with pytest.raises(ErrorDominio) as error:
        integrados.pasos(activar, "ERP", diagnostico, {})
    assert error.value.codigo == "WKR_EJE_006"


def test_iniciar_agente_y_sugeridos():
    agente = integrados.CATALOGO["iniciar_agente"]
    diagnostico = {"servicios": [{"nombre": "SQLAgent$ERP", "estado": "Stopped"}]}
    [paso] = integrados.pasos(agente, None, diagnostico, {"servicio": "SQLAgent$ERP"})
    assert "Start-Service -Name 'SQLAgent$ERP'" in paso.powershell
    # Un servicio del motor no es un Agent.
    with pytest.raises(ErrorDominio):
        integrados.pasos(
            agente,
            None,
            {"servicios": [{"nombre": "MSSQLSERVER", "estado": "Stopped"}]},
            {"servicio": "MSSQLSERVER"},
        )
    assert integrados.sugerido_para("evento:backup_full:ERP") == (
        "programar_backup_full",
        {"base": "ERP"},
    )
    assert integrados.sugerido_para("evento:backup_log:ERP")[0] == "programar_backup_log"
    assert integrados.sugerido_para("evento:job:ABC") == ("reintentar_job", {"job_id": "ABC"})
    assert integrados.sugerido_para("evento:agente") == ("iniciar_agente", {})
    assert integrados.sugerido_para("evento:job_colgado:ABC") is None


def test_inicio_sugerido_de_un_backup():
    from app.modulos.conexiones.entidades import Conexion

    conexion = replace(
        Conexion("c", "t", "A", "h", None, None, "DEV", "u", ""),
        ventana_inicio=hora(22, 0),
        ventana_fin=hora(6, 0),
        zona_horaria="America/Argentina/Buenos_Aires",
    )
    servidor = {"desfase_min": -180}  # el servidor también está en UTC-3
    # 15:00 en Buenos Aires: la próxima ventana empieza a las 22:00.
    tarde = datetime(2026, 11, 16, 18, 0, tzinfo=UTC)
    assert workarounds.inicio_sugerido(conexion, servidor, tarde) == "2026-11-16T22:00"
    # Con el servidor en UTC, la misma hora es la 01:00 del día siguiente.
    assert workarounds.inicio_sugerido(conexion, {"desfase_min": 0}, tarde) == "2026-11-17T01:00"
    # Dentro de la ventana: en unos minutos.
    noche = datetime(2026, 11, 17, 2, 3, tzinfo=UTC)  # 23:03 en Buenos Aires
    assert workarounds.inicio_sugerido(conexion, servidor, noche) == "2026-11-16T23:10"
    # Sin ventana: las 02:00 siguientes del servidor.
    sin_ventana = replace(conexion, ventana_inicio=None, ventana_fin=None)
    assert workarounds.inicio_sugerido(sin_ventana, servidor, tarde) == "2026-11-17T02:00"


# --- Servicios y API del monitor ----------------------------------------------------


async def test_politica_de_backup_y_estado_actual(monitor, monkeypatch):  # noqa: F811
    conexion = await _crear(monitor)
    assert (await servicios.obtener_politica(TENANT, conexion.id)) == PoliticaBackup()
    with pytest.raises(ErrorDominio) as error:
        await servicios.guardar_politica(TENANT, _actor("LECTURA"), conexion.id, POLITICA)
    assert error.value.codigo == "USR_PER_000"
    with pytest.raises(ErrorDominio) as error:
        await servicios.guardar_politica(
            TENANT, _actor(), conexion.id, PoliticaBackup(full_horas=0)
        )
    assert error.value.codigo == "MNT_CFG_001"
    nueva = PoliticaBackup(full_horas=168, log_minutos=15)
    await servicios.guardar_politica(TENANT, _actor(), conexion.id, nueva)
    assert monitor["politicas"][conexion.id] == nueva
    accion, detalle = monitor["eventos"][-1]
    assert accion == "monitor.politica_backup_modificada"
    assert detalle["anterior"]["full_horas"] == 24 and detalle["nueva"]["full_horas"] == 168

    async def obtener_estado(tenant_id, conexion_id):
        return {
            "estado": "ok",
            "backups": [_base(full_min=30 * 60, log_min=20)],
            "jobs": [_job(nombre="DBA Assistant · Backup full · ERP · x", resultado=None)],
            "agente": {"estado": "Stopped", "inicio": "Automatic"},
        }

    monkeypatch.setattr(repositorios, "obtener_estado", obtener_estado)
    actual = await servicios.estado_actual(TENANT, conexion.id)
    assert actual["politica_backup"]["full_horas"] == 168
    # Con la política semanal, un full de hace 30 h está al día; el log de 20 min, no.
    assert actual["backups"][0]["estado"] == {"full": "ok", "log": "warning"}
    job = actual["jobs"][0]
    assert job["backup_programado"] and job["etiqueta"] == "Backup full · ERP · x"
    assert job["estado"] == "sin_ejecuciones" and actual["agente_detenido"]


async def test_historiales_y_explicacion_de_un_job(monitor, monkeypatch):  # noqa: F811
    conexion = await _crear(monitor)
    llamadas = []

    def leer(c, funcion, *argumentos, base=None):
        llamadas.append((funcion.__name__ if hasattr(funcion, "__name__") else "f", argumentos))
        if funcion is lectura.historial_backups:
            return [{"tipo": "full"}]
        return [
            {"inicio": "2026-11-16T03:00:00", "resultado": "exito", "pasos": []},
            {"inicio": "2026-11-15T03:00:00", "resultado": "falla", "pasos": [{"m": "x"}]},
        ]

    monkeypatch.setattr(servicios, "_leer", leer)
    assert await servicios.historial_backups(TENANT, conexion.id, "ERP") == [{"tipo": "full"}]
    with pytest.raises(ErrorDominio):
        await servicios.historial_backups(TENANT, conexion.id, " ")
    job_id = _job()["id"]
    assert len(await servicios.historial_job(TENANT, conexion.id, job_id)) == 2
    with pytest.raises(ErrorDominio) as error:
        await servicios.historial_job(TENANT, conexion.id, "no-es-un-guid")
    assert error.value.codigo == "MNT_VAL_000"

    pedidos = []

    async def explicar_falla(nombre, ejecucion):
        pedidos.append((nombre, ejecucion))
        return servicios.analisis_ia.ExplicacionJob(resumen="r", causa="c", pasos=["p"])

    monkeypatch.setattr(servicios.analisis_ia, "explicar_falla", explicar_falla)
    with pytest.raises(ErrorDominio):
        await servicios.explicar_job(TENANT, _actor("LECTURA"), conexion.id, job_id)
    explicacion = await servicios.explicar_job(TENANT, _actor(), conexion.id, job_id)
    assert explicacion == {
        "inicio": "2026-11-15T03:00:00",
        "resumen": "r",
        "causa": "c",
        "pasos": ["p"],
    }
    assert pedidos[0][1]["resultado"] == "falla"
    # La misma falla no se vuelve a pedir a la IA.
    await servicios.explicar_job(TENANT, _actor(), conexion.id, job_id)
    assert len(pedidos) == 1


async def test_api_de_backups_y_jobs(monitor, monkeypatch):  # noqa: F811
    conexion = await _crear(monitor)
    monkeypatch.setattr(servicios, "_leer", lambda c, f, *a, base=None: [])
    consulta = await _navegador(monitor, "LECTURA")
    dba = await _navegador(monitor, "DBA")
    politica = consulta.get(f"/api/monitor/{conexion.id}/backups/politica").json()
    assert politica == {"habilitada": True, "full_horas": 24, "log_minutos": 60}
    denegado = consulta.put(
        f"/api/monitor/{conexion.id}/backups/politica", json=politica, headers=CABECERA
    )
    assert denegado.status_code == 403
    guardada = dba.put(
        f"/api/monitor/{conexion.id}/backups/politica",
        json={"habilitada": False, "full_horas": 48, "log_minutos": 30},
        headers=CABECERA,
    )
    assert guardada.json() == {"habilitada": False, "full_horas": 48, "log_minutos": 30}
    historial = consulta.get(f"/api/monitor/{conexion.id}/backups/historial?base=ERP")
    assert historial.json() == {"backups": []}
    job = consulta.get(f"/api/monitor/{conexion.id}/jobs/{_job()['id']}")
    assert job.json() == {"ejecuciones": []}
    sin_falla = dba.post(
        f"/api/monitor/{conexion.id}/jobs/{_job()['id']}/explicar", headers=CABECERA
    )
    assert sin_falla.json()["codigo"] == "MNT_VAL_000"
