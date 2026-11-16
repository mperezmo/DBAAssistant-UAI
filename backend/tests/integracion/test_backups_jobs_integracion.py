"""Backups, jobs del SQL Server Agent, backup programado (WK-09) y Query Store contra un
SQL Server real con el Agent activo."""

import os
import time
import uuid
from datetime import datetime, timedelta

import pytest

from app.core.secretos import proveedor_secretos
from app.datos import sqlserver
from app.modulos.conexiones import repositorios as conexiones
from app.modulos.monitor import lectura, query_store
from app.modulos.workarounds import integrados

pytestmark = pytest.mark.integracion

SERVIDOR = (
    f"{os.environ.get('SQLSERVER_HOST', 'localhost')},{os.environ.get('SQLSERVER_PUERTO', '14330')}"
)
ESPERA_SEGUNDOS = 60


def _password() -> str:
    return proveedor_secretos().obtener("sqlserver_password")


def _esperar(condicion) -> None:
    limite = time.monotonic() + ESPERA_SEGUNDOS
    while not condicion():
        assert time.monotonic() < limite, "tiempo de espera agotado"
        time.sleep(1)


def _con_agente(cursor) -> None:
    agente = lectura.leer_agente(cursor)
    if not agente or agente["estado"] != "Running":
        pytest.skip("SQL Server Agent no está activo en la instancia de pruebas")


def test_backups_jobs_y_backup_programado(prefijo_aislado):
    base = f"{prefijo_aislado.sqlserver_prefijo_base}_Backups"
    sqlserver.crear_base_si_no_existe(base)
    with sqlserver.conectar_externo(SERVIDOR, "sa", _password(), base) as abierta:
        abierta.autocommit = True
        cursor = abierta.cursor()
        cursor.execute(f"ALTER DATABASE [{base}] SET RECOVERY FULL")
        _con_agente(cursor)
        assert conexiones.datos_de_instancia(SERVIDOR, "sa", _password())[3] == ()

        [sin_backup] = [b for b in lectura.leer_backups(cursor) if b["base"] == base]
        assert sin_backup["full_min"] is None and sin_backup["recuperacion"] == "FULL"

        # Un job que falla queda con su error y su historial.
        nombre_job = f"Prueba falla {uuid.uuid4().hex[:8]}"
        cursor.execute(
            "EXEC msdb.dbo.sp_add_job @job_name = ?; "
            "EXEC msdb.dbo.sp_add_jobstep @job_name = ?, @step_name = N'Divide', "
            "@subsystem = N'TSQL', @command = N'SELECT 1/0'; "
            "EXEC msdb.dbo.sp_add_jobserver @job_name = ?; "
            "EXEC msdb.dbo.sp_start_job @job_name = ?",
            nombre_job,
            nombre_job,
            nombre_job,
            nombre_job,
        )
        try:

            def falla_registrada():
                jobs = [j for j in lectura.leer_jobs(cursor) if j["nombre"] == nombre_job]
                return jobs and jobs[0]["resultado"] == "falla"

            _esperar(falla_registrada)
            [job] = [j for j in lectura.leer_jobs(cursor) if j["nombre"] == nombre_job]
            assert "Divide" in job["error"] and job["habilitado"]
            [ejecucion] = lectura.historial_job(abierta, job["id"])
            assert ejecucion["resultado"] == "falla" and ejecucion["pasos"][0]["nombre"] == "Divide"
            diagnostico = integrados.diagnosticar(integrados.CATALOGO["reintentar_job"], abierta)
            assert job["id"] in {j["id"] for j in diagnostico["jobs"]}
        finally:
            cursor.execute("EXEC msdb.dbo.sp_delete_job @job_name = ?", nombre_job)

        # WK-09: el job queda programado; al correr (acá se lo inicia a mano) hace el
        # backup COPY_ONLY y se borra solo.
        full = integrados.CATALOGO["programar_backup_full"]
        diagnostico = integrados.diagnosticar(full, abierta)
        assert diagnostico["agente"] == "Running" and not diagnostico["cadena_log"]
        manana = datetime.strptime(diagnostico["ahora_servidor"], integrados.FORMATO_INICIO)
        inicio = (manana + timedelta(days=1)).strftime(integrados.FORMATO_INICIO)
        [paso] = integrados.pasos(full, base, diagnostico, {"inicio": inicio})
        cursor.execute(paso.sql)
        nombre = paso.descripcion.split(" → ")[0]
        programado = integrados.diagnosticar(full, abierta)["programados"]
        assert [p["nombre"] for p in programado] == [nombre]
        cursor.execute("EXEC msdb.dbo.sp_start_job @job_name = ?", nombre)

        def respaldada():
            [actual] = [b for b in lectura.leer_backups(cursor) if b["base"] == base]
            return actual["full_min"] is not None

        _esperar(respaldada)
        _esperar(lambda: not integrados.diagnosticar(full, abierta)["programados"])
        [ultimo, *_] = lectura.historial_backups(abierta, base)
        assert ultimo["tipo"] == "full" and ultimo["copy_only"]
        # Un full COPY_ONLY no inicia la cadena de log: el backup de log no se programa.
        log = integrados.CATALOGO["programar_backup_log"]
        with pytest.raises(Exception, match="WKR_EJE_005"):
            integrados.pasos(log, base, integrados.diagnosticar(log, abierta), {"inicio": inicio})


def test_query_store_real(prefijo_aislado):
    base = f"{prefijo_aislado.sqlserver_prefijo_base}_QueryStore"
    sqlserver.crear_base_si_no_existe(base)
    with sqlserver.conectar_externo(SERVIDOR, "sa", _password(), base) as abierta:
        abierta.autocommit = True
        cursor = abierta.cursor()
        cursor.execute(f"ALTER DATABASE [{base}] SET QUERY_STORE = OFF")
        activar = integrados.CATALOGO["activar_query_store"]
        diagnostico = integrados.diagnosticar(activar, abierta)
        assert diagnostico["estado"] == "OFF"
        [paso] = integrados.pasos(activar, base, diagnostico, {})
        cursor.execute(paso.sql)
        assert integrados.diagnosticar(activar, abierta)["estado"] == "READ_WRITE"
        # La captura AUTO ignora consultas poco frecuentes: para la prueba se captura todo.
        cursor.execute(f"ALTER DATABASE [{base}] SET QUERY_STORE (QUERY_CAPTURE_MODE = ALL)")

        cursor.execute(
            "CREATE TABLE dbo.ventas (id INT IDENTITY PRIMARY KEY, cliente INT, monto MONEY)"
        )
        cursor.execute(
            "INSERT dbo.ventas (cliente, monto) SELECT TOP (5000) o.object_id % 50, 10 "
            "FROM sys.all_objects o CROSS JOIN sys.all_objects p"
        )
        for cliente in range(30):
            cursor.execute("SELECT SUM(monto) FROM dbo.ventas WHERE cliente = ?", cliente)
            cursor.fetchall()

        consultas = query_store.consultas_de_base(abierta, "ejecuciones", "1h")
        assert consultas["estado"] == "READ_WRITE"
        [ventas] = [c for c in consultas["consultas"] if "SUM(monto)" in c["texto"]]
        assert ventas["ejecuciones"] >= 30 and ventas["planes"] >= 1
        assert not any("dba-assistant:monitor" in c["texto"] for c in consultas["consultas"])
        detalle = query_store.detalle(cursor, ventas["query_id"], "duracion", "24h")
        assert detalle["planes"] and detalle["texto"] == ventas["texto"]

    with sqlserver.conectar_externo(SERVIDOR, "sa", _password()) as maestra:
        estado = query_store.estado_bases(maestra)
        assert estado["disponible"]
        assert {"base": base} | {"estado": "READ_WRITE"} == {
            "base": base,
            "estado": next(b["estado"] for b in estado["bases"] if b["base"] == base),
        }
