"""Lectura de métricas de una instancia SQL Server mediante vistas de administración (DMV).

El login de la conexión necesita el permiso ``VIEW SERVER STATE``. Las funciones son
sincrónicas (pyodbc): el recolector las ejecuta en hilos aparte.
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

# Leer el archivo de eventos es costoso: los deadlocks se actualizan cada minuto.
INTERVALO_DEADLOCKS_SEGUNDOS = 60
GB = 1024**3


@dataclass
class EstadoLectura:
    """Datos que se conservan entre lecturas sucesivas de una misma instancia."""

    logins: int | None = None
    logins_en: float | None = None
    deadlocks: int | None = None
    deadlocks_en: float | None = None


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

    if estado.deadlocks_en is None or ahora - estado.deadlocks_en >= INTERVALO_DEADLOCKS_SEGUNDOS:
        estado.deadlocks = cursor.execute(CONSULTA_DEADLOCKS).fetchone()[0]
        estado.deadlocks_en = ahora

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
    )


def consultas_costosas(conexion: pyodbc.Connection) -> list[dict]:
    """Las cinco consultas con mayor consumo de CPU acumulado desde el inicio del motor."""
    return [
        {"cpu_ms": int(cpu_ms), "ejecuciones": int(ejecuciones), "texto": (texto or "").strip()}
        for cpu_ms, ejecuciones, texto in conexion.cursor().execute(CONSULTA_COSTOSAS).fetchall()
    ]
