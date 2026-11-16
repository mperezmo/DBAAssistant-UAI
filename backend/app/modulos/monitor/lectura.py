"""Lectura de métricas de una instancia SQL Server mediante vistas de administración (DMV).

El login de la conexión necesita el permiso ``VIEW SERVER STATE``; para backups y jobs del
SQL Server Agent, además, lectura sobre ``msdb``. Las funciones son sincrónicas (pyodbc):
el recolector las ejecuta en hilos aparte.
"""

from dataclasses import dataclass

import pyodbc

from app.modulos.monitor.entidades import Lectura

# Marca las consultas propias para excluirlas del ranking de consultas costosas.
MARCA = "/* dba-assistant:monitor */"

# % de CPU del proceso de SQL Server. El ring buffer se actualiza una vez por minuto.
CONSULTA_CPU = f"""{MARCA}
SELECT TOP (1) registro.value(
    '(./Record/SchedulerMonitorEvent/SystemHealth/ProcessUtilization)[1]', 'int')
FROM (SELECT CONVERT(XML, record) AS registro, [timestamp]
      FROM sys.dm_os_ring_buffers
      WHERE ring_buffer_type = N'RING_BUFFER_SCHEDULER_MONITOR'
        AND record LIKE N'%<SystemHealth>%') AS rb
ORDER BY [timestamp] DESC"""

CONSULTA_MEMORIA = f"""{MARCA}
SELECT total_physical_memory_kb, available_physical_memory_kb FROM sys.dm_os_sys_memory"""

# Volúmenes que alojan archivos de las bases.
CONSULTA_VOLUMENES = f"""{MARCA}
SELECT DISTINCT COALESCE(vs.volume_mount_point, vs.logical_volume_name),
       vs.total_bytes, vs.available_bytes
FROM sys.master_files AS mf
CROSS APPLY sys.dm_os_volume_stats(mf.database_id, mf.file_id) AS vs"""

# Sesiones de usuario activas, duración promedio de las consultas en curso (ms) y el
# contador acumulado de logins, del que se deriva la tasa de conexiones por segundo.
CONSULTA_ACTIVIDAD = f"""{MARCA}
SELECT
    (SELECT COUNT(*) FROM sys.dm_exec_sessions
     WHERE is_user_process = 1 AND status <> 'sleeping' AND session_id <> @@SPID),
    (SELECT AVG(CAST(r.total_elapsed_time AS BIGINT))
     FROM sys.dm_exec_requests AS r
     JOIN sys.dm_exec_sessions AS s ON s.session_id = r.session_id
     WHERE s.is_user_process = 1 AND r.session_id <> @@SPID),
    (SELECT cntr_value FROM sys.dm_os_performance_counters
     WHERE counter_name = 'Logins/sec' AND object_name LIKE '%General Statistics%')"""

# Sesiones que esperan un lock tomado por otra.
CONSULTA_BLOQUEOS = f"""{MARCA}
SELECT r.session_id, r.blocking_session_id, DB_NAME(r.database_id),
       r.wait_resource, r.wait_type, r.wait_time
FROM sys.dm_exec_requests AS r
WHERE r.blocking_session_id <> 0
ORDER BY r.wait_time DESC"""

# Bases que no están disponibles (OFFLINE, SUSPECT, RECOVERY_PENDING, etc.).
CONSULTA_BASES = f"""{MARCA}
SELECT name, state_desc FROM sys.databases WHERE state_desc <> 'ONLINE'"""

# % de uso del log de transacciones por base.
CONSULTA_LOGS = f"""{MARCA}
SELECT RTRIM(instance_name), cntr_value
FROM sys.dm_os_performance_counters
WHERE counter_name = 'Percent Log Used' AND object_name LIKE '%:Databases%'
  AND instance_name NOT IN ('_Total', 'mssqlsystemresource')"""

# Deadlocks de las últimas 24 h, registrados por la sesión de eventos system_health.
CONSULTA_DEADLOCKS = f"""{MARCA}
SELECT COUNT(*)
FROM sys.fn_xe_file_target_read_file('system_health*.xel', NULL, NULL, NULL)
WHERE object_name = 'xml_deadlock_report'
  AND timestamp_utc > DATEADD(HOUR, -24, SYSUTCDATETIME())"""

CONSULTA_COSTOSAS = f"""{MARCA}
SELECT TOP (5) qs.total_worker_time / 1000, qs.execution_count,
       SUBSTRING(st.text, qs.statement_start_offset / 2 + 1,
           (CASE qs.statement_end_offset WHEN -1 THEN DATALENGTH(st.text)
                 ELSE qs.statement_end_offset END - qs.statement_start_offset) / 2 + 1)
FROM sys.dm_exec_query_stats AS qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) AS st
WHERE st.text NOT LIKE '%dba-assistant:monitor%'
ORDER BY qs.total_worker_time DESC"""

# Último backup full, diferencial y de log de cada base (minutos transcurridos). El GUID
# descarta los backups de una base anterior con el mismo nombre; los 40 días acotan la
# lectura de msdb (la política admite hasta 31 días entre backups full).
CONSULTA_BACKUPS = f"""{MARCA}
SELECT d.name, d.recovery_model_desc, DATEDIFF(MINUTE, d.create_date, GETDATE()),
       DATEDIFF(MINUTE, MAX(CASE WHEN b.type = 'D' THEN b.backup_finish_date END), GETDATE()),
       DATEDIFF(MINUTE, MAX(CASE WHEN b.type = 'I' THEN b.backup_finish_date END), GETDATE()),
       DATEDIFF(MINUTE, MAX(CASE WHEN b.type = 'L' THEN b.backup_finish_date END), GETDATE())
FROM sys.databases AS d
JOIN sys.database_recovery_status AS rs ON rs.database_id = d.database_id
LEFT JOIN msdb.dbo.backupset AS b
    ON b.database_name = d.name AND b.database_guid = rs.database_guid
   AND b.backup_finish_date >= DATEADD(DAY, -40, GETDATE())
WHERE d.database_id <> 2 AND d.name <> N'model' AND d.state_desc = 'ONLINE'
  AND d.source_database_id IS NULL
GROUP BY d.name, d.recovery_model_desc, d.create_date
ORDER BY d.name"""

CONSULTA_HISTORIAL_BACKUPS = f"""{MARCA}
SELECT TOP (10) b.type, b.is_copy_only, CONVERT(VARCHAR(19), b.backup_finish_date, 126),
       DATEDIFF(SECOND, b.backup_start_date, b.backup_finish_date),
       b.backup_size, b.compressed_backup_size, m.physical_device_name, b.user_name
FROM msdb.dbo.backupset AS b
OUTER APPLY (SELECT TOP (1) physical_device_name FROM msdb.dbo.backupmediafamily
             WHERE media_set_id = b.media_set_id ORDER BY family_sequence_number) AS m
WHERE b.database_name = ?
ORDER BY b.backup_set_id DESC"""

# Servicio SQL Server Agent (no existe en todas las ediciones; en Express no corre).
CONSULTA_AGENTE = f"""{MARCA}
SELECT status_desc, startup_type_desc FROM sys.dm_server_services
WHERE servicename LIKE N'SQL Server Agent%'"""

# Jobs con su última ejecución, el error del paso que falló, si están corriendo (en la
# sesión actual del Agent), la duración promedio de sus ejecuciones exitosas y la próxima.
_SEGUNDOS = "run_duration / 10000 * 3600 + run_duration / 100 % 100 * 60 + run_duration % 100"
CONSULTA_JOBS = f"""{MARCA}
WITH sesion AS (SELECT MAX(session_id) AS id FROM msdb.dbo.syssessions)
SELECT CONVERT(NVARCHAR(36), j.job_id), j.name, j.enabled, h.run_status,
       DATEDIFF(MINUTE, DATETIMEFROMPARTS(h.run_date / 10000, h.run_date / 100 % 100,
           h.run_date % 100, h.run_time / 10000, h.run_time / 100 % 100, h.run_time % 100, 0),
           GETDATE()),
       h.duracion_s, paso.mensaje,
       CASE WHEN a.start_execution_date IS NOT NULL AND a.stop_execution_date IS NULL
            THEN DATEDIFF(SECOND, a.start_execution_date, GETDATE()) END,
       prom.promedio_s, CONVERT(VARCHAR(16), a.next_scheduled_run_date, 126)
FROM msdb.dbo.sysjobs AS j
OUTER APPLY (SELECT TOP (1) instance_id, run_status, run_date, run_time,
                    {_SEGUNDOS} AS duracion_s
             FROM msdb.dbo.sysjobhistory WHERE job_id = j.job_id AND step_id = 0
             ORDER BY instance_id DESC) AS h
OUTER APPLY (SELECT TOP (1) step_name + N': ' + message AS mensaje
             FROM msdb.dbo.sysjobhistory
             WHERE job_id = j.job_id AND step_id > 0 AND run_status = 0 AND h.run_status = 0
               AND instance_id < h.instance_id
             ORDER BY instance_id DESC) AS paso
OUTER APPLY (SELECT TOP (1) start_execution_date, stop_execution_date, next_scheduled_run_date
             FROM msdb.dbo.sysjobactivity
             WHERE job_id = j.job_id AND session_id = (SELECT id FROM sesion)) AS a
OUTER APPLY (SELECT AVG(x.s) AS promedio_s
             FROM (SELECT TOP (10) {_SEGUNDOS} AS s FROM msdb.dbo.sysjobhistory
                   WHERE job_id = j.job_id AND step_id = 0 AND run_status = 1
                   ORDER BY instance_id DESC) AS x) AS prom
ORDER BY j.name"""

# Historial de un job: ejecuciones (step_id 0) con sus pasos.
CONSULTA_HISTORIAL_JOB = f"""{MARCA}
SELECT TOP (300) step_id, step_name, run_status,
       CONVERT(VARCHAR(19), DATETIMEFROMPARTS(run_date / 10000, run_date / 100 % 100,
           run_date % 100, run_time / 10000, run_time / 100 % 100, run_time % 100, 0), 126),
       {_SEGUNDOS}, message
FROM msdb.dbo.sysjobhistory
WHERE job_id = ?
ORDER BY instance_id DESC"""

# Leer el archivo de eventos es costoso: los deadlocks se actualizan cada minuto.
INTERVALO_DEADLOCKS_SEGUNDOS = 60
# Backups y jobs cambian poco: no hace falta leerlos en cada ciclo.
INTERVALO_BACKUPS_SEGUNDOS = 300
INTERVALO_JOBS_SEGUNDOS = 60
GB = 1024**3
# run_status de msdb.dbo.sysjobhistory.
RESULTADOS_JOB = {0: "falla", 1: "exito", 2: "reintento", 3: "cancelado", 4: "en_curso"}
PREFIJO_BACKUPS = "evento:backup_"
PREFIJOS_AGENTE = ("evento:job", "evento:agente")


@dataclass
class EstadoLectura:
    """Datos que se conservan entre lecturas sucesivas de una misma instancia."""

    logins: int | None = None
    logins_en: float | None = None
    deadlocks: int | None = None
    deadlocks_en: float | None = None
    backups: list[dict] | None = None
    backups_en: float | None = None
    jobs: list[dict] | None = None
    agente: dict | None = None
    jobs_en: float | None = None


def _porcentaje(usado: float, total: float) -> float | None:
    return round(usado * 100 / total, 1) if total else None


def _modo_lock(tipo_espera: str | None) -> str:
    # LCK_M_X → X; otras esperas se informan tal cual.
    tipo = tipo_espera or ""
    return tipo.removeprefix("LCK_M_") if tipo.startswith("LCK_M_") else tipo


def leer(conexion: pyodbc.Connection, estado: EstadoLectura, ahora: float) -> Lectura:
    """Lee las métricas actuales; ``ahora`` es un reloj monótono en segundos."""
    cursor = conexion.cursor()

    fila = cursor.execute(CONSULTA_CPU).fetchone()
    cpu = float(fila[0]) if fila and fila[0] is not None else None

    total_kb, disponible_kb = cursor.execute(CONSULTA_MEMORIA).fetchone()
    memoria = _porcentaje(total_kb - disponible_kb, total_kb)

    volumenes = [
        {
            "volumen": nombre or "/",
            "total_gb": round(total / GB, 1),
            "libre_gb": round(libre / GB, 1),
            "uso": _porcentaje(total - libre, total),
        }
        for nombre, total, libre in cursor.execute(CONSULTA_VOLUMENES).fetchall()
    ]
    disco = max((v["uso"] for v in volumenes if v["uso"] is not None), default=None)

    sesiones, latencia, logins = cursor.execute(CONSULTA_ACTIVIDAD).fetchone()
    conexiones_seg = None
    # Si el contador bajó, la instancia se reinició: se descarta ese intervalo.
    if estado.logins is not None and logins is not None and logins >= estado.logins:
        transcurrido = ahora - estado.logins_en
        if transcurrido > 0:
            conexiones_seg = round((logins - estado.logins) / transcurrido, 1)
    estado.logins, estado.logins_en = logins, ahora

    bloqueos = [
        {
            "sid": sid,
            "bloqueador": bloqueador,
            "base": base,
            "recurso": recurso,
            "modo": _modo_lock(tipo),
            "espera_s": round((espera or 0) / 1000, 1),
        }
        for sid, bloqueador, base, recurso, tipo, espera in cursor.execute(
            CONSULTA_BLOQUEOS
        ).fetchall()
    ]

    bases = [
        {"base": nombre, "estado": estado_base}
        for nombre, estado_base in cursor.execute(CONSULTA_BASES).fetchall()
    ]
    logs = [
        {"base": nombre, "uso": float(uso)}
        for nombre, uso in cursor.execute(CONSULTA_LOGS).fetchall()
    ]

    if estado.deadlocks_en is None or ahora - estado.deadlocks_en >= INTERVALO_DEADLOCKS_SEGUNDOS:
        estado.deadlocks = cursor.execute(CONSULTA_DEADLOCKS).fetchone()[0]
        estado.deadlocks_en = ahora

    # Backups y jobs viven en msdb y pueden requerir permisos que el login no tenga: si
    # fallan, el resto de la lectura vale igual y sus alertas quedan sin evaluar.
    if estado.backups_en is None or ahora - estado.backups_en >= INTERVALO_BACKUPS_SEGUNDOS:
        estado.backups_en = ahora
        try:
            estado.backups = leer_backups(cursor)
        except pyodbc.Error:
            estado.backups = None
    if estado.jobs_en is None or ahora - estado.jobs_en >= INTERVALO_JOBS_SEGUNDOS:
        estado.jobs_en = ahora
        try:
            estado.agente = leer_agente(cursor)
            estado.jobs = leer_jobs(cursor)
        except pyodbc.Error:
            estado.agente, estado.jobs = None, None
    no_evaluadas = ((PREFIJO_BACKUPS,) if estado.backups is None else ()) + (
        PREFIJOS_AGENTE if estado.jobs is None else ()
    )

    return Lectura(
        valores={
            "cpu": cpu,
            "memoria": memoria,
            "disco": disco,
            "latencia": float(latencia) if latencia is not None else 0.0,
            "sesiones": float(sesiones),
            "bloqueos": float(len(bloqueos)),
            "deadlocks": float(estado.deadlocks) if estado.deadlocks is not None else None,
            "conexiones_seg": conexiones_seg,
        },
        bloqueos=bloqueos,
        volumenes=volumenes,
        bases_no_disponibles=bases,
        logs=logs,
        backups=estado.backups,
        jobs=estado.jobs,
        agente=estado.agente,
        no_evaluadas=no_evaluadas,
    )


def leer_backups(cursor: pyodbc.Cursor) -> list[dict]:
    """Minutos desde el último backup de cada tipo (None: no hubo en los últimos 40 días)."""
    return [
        {
            "base": f[0],
            "recuperacion": f[1],
            "creada_min": f[2],
            "full_min": f[3],
            "diferencial_min": f[4],
            "log_min": f[5],
        }
        for f in cursor.execute(CONSULTA_BACKUPS).fetchall()
    ]


def leer_agente(cursor: pyodbc.Cursor) -> dict | None:
    fila = cursor.execute(CONSULTA_AGENTE).fetchone()
    return {"estado": fila[0], "inicio": fila[1]} if fila else None


def leer_jobs(cursor: pyodbc.Cursor) -> list[dict]:
    return [
        {
            "id": f[0],
            "nombre": f[1],
            "habilitado": bool(f[2]),
            "resultado": RESULTADOS_JOB.get(f[3]) if f[3] is not None else None,
            "hace_min": f[4],
            "duracion_s": f[5],
            "error": (f[6] or "").strip()[:1000] or None,
            "corriendo_s": f[7],
            "promedio_s": f[8],
            "proxima": f[9],
        }
        for f in cursor.execute(CONSULTA_JOBS).fetchall()
    ]


def historial_backups(conexion: pyodbc.Connection, base: str) -> list[dict]:
    tipos = {"D": "full", "I": "diferencial", "L": "log"}
    return [
        {
            "tipo": tipos.get(f[0], f[0]),
            "copy_only": bool(f[1]),
            "fin": f[2],
            "duracion_s": f[3],
            "tamano_mb": round(float(f[4] or 0) / 1048576, 1),
            "comprimido_mb": round(float(f[5]) / 1048576, 1) if f[5] else None,
            "archivo": f[6],
            "usuario": f[7],
        }
        for f in conexion.cursor().execute(CONSULTA_HISTORIAL_BACKUPS, base).fetchall()
    ]


def historial_job(conexion: pyodbc.Connection, job_id: str, maximo: int = 20) -> list[dict]:
    """Últimas ejecuciones de un job, cada una con sus pasos (de la más reciente a la más
    vieja). En msdb cada ejecución termina con una fila de resultado (paso 0) y sus pasos
    quedan antes que ella."""
    ejecuciones: list[dict] = []
    actual: dict | None = None
    for paso, nombre, estado, inicio, duracion, mensaje in (
        conexion.cursor().execute(CONSULTA_HISTORIAL_JOB, job_id).fetchall()
    ):
        if paso == 0:
            if len(ejecuciones) == maximo:
                break
            actual = {
                "inicio": inicio,
                "duracion_s": duracion,
                "resultado": RESULTADOS_JOB.get(estado),
                "mensaje": (mensaje or "").strip(),
                "pasos": [],
            }
            ejecuciones.append(actual)
        elif actual is not None:
            actual["pasos"].insert(
                0,
                {
                    "paso": paso,
                    "nombre": nombre,
                    "resultado": RESULTADOS_JOB.get(estado),
                    "inicio": inicio,
                    "duracion_s": duracion,
                    "mensaje": (mensaje or "").strip(),
                },
            )
    return ejecuciones


def consultas_costosas(conexion: pyodbc.Connection) -> list[dict]:
    """Las cinco consultas con mayor consumo de CPU acumulado desde el inicio del motor."""
    return [
        {"cpu_ms": int(cpu_ms), "ejecuciones": int(ejecuciones), "texto": (texto or "").strip()}
        for cpu_ms, ejecuciones, texto in conexion.cursor().execute(CONSULTA_COSTOSAS).fetchall()
    ]
