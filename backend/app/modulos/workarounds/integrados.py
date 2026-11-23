"""Workarounds integrados: playbooks de remediación provistos por la plataforma.

Cada uno tiene un diagnóstico de solo lectura, que se muestra antes de confirmar, y
genera sus pasos a partir de ese diagnóstico y de lo que elige el usuario. No se
validan en Sandbox: es código revisado y el Sandbox (solo estructura, sin carga real)
no aporta nada para sesiones, logs o fragmentación.
"""

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import pyodbc

from app.core.errores import ErrorDominio
from app.identidad.entidades import AMBIENTES, ROLES_OPERATIVOS
from app.modulos.monitor import lectura as monitor
from app.modulos.monitor.entidades import PREFIJO_JOB_BACKUP, RECUPERACION_CON_LOG, estado_job
from app.modulos.workarounds.entidades import Paso
from app.web.i18n import traducir

TODOS = ROLES_OPERATIVOS


@dataclass(frozen=True)
class Integrado:
    id: str
    codigo: str
    categoria: str
    criticidad: str
    roles: tuple[str, ...]
    requiere_base: bool
    # Fuera de la ventana de mantenimiento exige la autorización de otro usuario.
    requiere_ventana: bool = False
    # Puede ejecutarse solo ante un evento, si un DBA lo configura.
    eventos_automaticos: tuple[str, ...] = ()
    ambientes: tuple[str, ...] = AMBIENTES
    transaccional: bool = False

    @property
    def riesgo(self) -> str:
        return RIESGO_INTEGRADOS[self.id]

    # Nombre y descripción en el idioma de quien los ve (workarounds.integrado.<id>.*).
    @property
    def nombre(self) -> str:
        return traducir(f"workarounds.integrado.{self.id}.nombre")

    @property
    def descripcion(self) -> str:
        return traducir(f"workarounds.integrado.{self.id}.descripcion")


# Riesgo de cada integrado: alto si corta sesiones o toca la disponibilidad (servicio,
# Agent, base online), medio si afecta el rendimiento mientras corre, bajo si no impacta.
RIESGO_INTEGRADOS = {
    "kill_bloqueos": "alto",
    "shrink_log": "medio",
    "rebuild_indices": "medio",
    "update_statistics": "bajo",
    "limpiar_plan_cache": "medio",
    "checkpoint": "bajo",
    "iniciar_servicio": "alto",
    "base_online": "alto",
    "programar_backup_full": "bajo",
    "programar_backup_log": "bajo",
    "reintentar_job": "medio",
    "iniciar_agente": "alto",
    "activar_query_store": "bajo",
}

CATALOGO: dict[str, Integrado] = {
    i.id: i
    for i in (
        Integrado(
            "kill_bloqueos",
            "WK-01",
            "performance",
            "critical",
            TODOS,
            requiere_base=False,
        ),
        Integrado(
            "shrink_log",
            "WK-02",
            "espacio",
            "warning",
            TODOS,
            requiere_base=True,
            eventos_automaticos=("log",),
        ),
        Integrado(
            "rebuild_indices",
            "WK-03",
            "performance",
            "warning",
            TODOS,
            requiere_base=True,
            requiere_ventana=True,
        ),
        Integrado(
            "update_statistics",
            "WK-04",
            "mantenimiento",
            "info",
            TODOS,
            requiere_base=True,
            requiere_ventana=True,
        ),
        Integrado(
            "limpiar_plan_cache",
            "WK-05",
            "performance",
            "warning",
            TODOS,
            requiere_base=True,
        ),
        Integrado(
            "checkpoint",
            "WK-06",
            "mantenimiento",
            "info",
            TODOS,
            requiere_base=True,
            eventos_automaticos=("log",),
        ),
        Integrado(
            "iniciar_servicio",
            "WK-07",
            "servicio",
            "critical",
            TODOS,
            requiere_base=False,
            eventos_automaticos=("instancia",),
        ),
        Integrado(
            "base_online",
            "WK-08",
            "servicio",
            "critical",
            TODOS,
            requiere_base=False,
            eventos_automaticos=("base",),
        ),
        # Los backups no corren en el momento: quedan programados como un job del SQL
        # Server Agent para una hora con menos carga (por defecto, la próxima ventana).
        Integrado(
            "programar_backup_full",
            "WK-09",
            "mantenimiento",
            "warning",
            TODOS,
            requiere_base=True,
        ),
        Integrado(
            "programar_backup_log",
            "WK-10",
            "mantenimiento",
            "warning",
            TODOS,
            requiere_base=True,
        ),
        Integrado(
            "reintentar_job",
            "WK-11",
            "servicio",
            "warning",
            TODOS,
            requiere_base=False,
        ),
        Integrado(
            "iniciar_agente",
            "WK-12",
            "servicio",
            "critical",
            TODOS,
            requiere_base=False,
        ),
        Integrado(
            "activar_query_store",
            "WK-13",
            "performance",
            "info",
            TODOS,
            requiere_base=True,
        ),
    )
}

PATRON_SERVICIO = re.compile(r"^MSSQL(SERVER|\$[A-Za-z0-9_]{1,16})$")
PATRON_AGENTE = re.compile(r"^(SQLSERVERAGENT|SQLAgent\$[A-Za-z0-9_]{1,16})$")
PATRON_JOB = re.compile(r"^[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$")
# Ediciones sin SQL Server Agent (EngineEdition 4: Express) y sin compresión de backups.
EDICION_EXPRESS = 4
# Los backups programados se aceptan hasta un mes adelante.
MAXIMO_DIAS_PROGRAMACION = 31
FORMATO_INICIO = "%Y-%m-%dT%H:%M"
# Query Store: configuración recomendada al activarlo.
QUERY_STORE_MB_MINIMO = 1024
QUERY_STORE_MB_MAXIMO = 10240
QUERY_STORE_DIAS = 30
VERSION_QUERY_STORE = 13  # SQL Server 2016
UMBRAL_FRAGMENTACION = 30
MINIMO_PAGINAS = 1000
UMBRAL_MODIFICACIONES = 0.2
MARGEN_LOG = 1.2
MINIMO_LOG_MB = 64
# Ediciones que admiten REBUILD ONLINE: Enterprise/Developer, Azure SQL y Managed Instance.
EDICIONES_ONLINE = (3, 5, 8)


def citar(nombre: str) -> str:
    """Equivalente a QUOTENAME para identificadores armados en el servidor."""
    return "[" + nombre.replace("]", "]]") + "]"


def literal(texto: str) -> str:
    return "N'" + texto.replace("'", "''") + "'"


# --- Diagnósticos (solo lectura) ----------------------------------------------------

CONSULTA_BLOQUEADORES = """
WITH esperas AS (
    SELECT session_id, blocking_session_id, wait_time
    FROM sys.dm_exec_requests WHERE blocking_session_id <> 0
)
SELECT s.session_id, s.login_name, s.host_name, s.program_name, DB_NAME(s.database_id),
       (SELECT COUNT(*) FROM esperas e WHERE e.blocking_session_id = s.session_id),
       (SELECT MAX(wait_time) FROM esperas e WHERE e.blocking_session_id = s.session_id),
       CASE WHEN s.session_id IN (SELECT session_id FROM esperas) THEN 0 ELSE 1 END,
       (SELECT TOP (1) t.text FROM sys.dm_exec_connections c
        CROSS APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) t
        WHERE c.session_id = s.session_id)
FROM sys.dm_exec_sessions AS s
WHERE s.session_id IN (SELECT blocking_session_id FROM esperas)
  AND s.is_user_process = 1 AND s.session_id <> @@SPID
ORDER BY 6 DESC
"""

CONSULTA_LOG = """
SELECT d.recovery_model_desc, d.log_reuse_wait_desc, f.name,
       f.size * 8 / 1024, u.used_log_space_in_bytes / 1048576, u.used_log_space_in_percent
FROM sys.databases AS d
CROSS JOIN sys.database_files AS f
CROSS JOIN sys.dm_db_log_space_usage AS u
WHERE d.database_id = DB_ID() AND f.type = 1
"""

CONSULTA_FRAGMENTACION = f"""
SELECT TOP (50) s.name, o.name, i.name, ips.avg_fragmentation_in_percent, ips.page_count,
       SERVERPROPERTY('EngineEdition')
FROM sys.dm_db_index_physical_stats(DB_ID(), NULL, NULL, NULL, 'LIMITED') AS ips
JOIN sys.indexes AS i ON i.object_id = ips.object_id AND i.index_id = ips.index_id
JOIN sys.objects AS o ON o.object_id = i.object_id
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE ips.avg_fragmentation_in_percent > {UMBRAL_FRAGMENTACION}
  AND ips.page_count >= {MINIMO_PAGINAS} AND i.name IS NOT NULL AND o.is_ms_shipped = 0
ORDER BY ips.avg_fragmentation_in_percent DESC
"""

CONSULTA_ESTADISTICAS = f"""
SELECT TOP (100) s.name, o.name, st.name, sp.rows, sp.modification_counter, sp.last_updated
FROM sys.stats AS st
JOIN sys.objects AS o ON o.object_id = st.object_id
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
CROSS APPLY sys.dm_db_stats_properties(st.object_id, st.stats_id) AS sp
WHERE o.is_ms_shipped = 0 AND sp.rows > 0
  AND sp.modification_counter > sp.rows * {UMBRAL_MODIFICACIONES}
ORDER BY sp.modification_counter DESC
"""

# Se lee desde master: la base afectada no admite conexiones.
CONSULTA_BASES_NO_DISPONIBLES = """
SELECT name, state_desc FROM sys.databases WHERE state_desc <> 'ONLINE' ORDER BY name
"""
# SET ONLINE resuelve una base puesta offline. SUSPECT, RECOVERY_PENDING o EMERGENCY
# necesitan diagnóstico manual (archivos, DBCC CHECKDB): forzarlas puede perder datos.
ESTADO_RECUPERABLE = "OFFLINE"

# Datos para programar un backup de la base actual: hora del servidor (los jobs se
# programan en hora local de la instancia), Agent, edición y backups programados.
CONSULTA_BACKUP = """
SELECT d.recovery_model_desc, CAST(SERVERPROPERTY('EngineEdition') AS INT),
       CAST(SERVERPROPERTY('Edition') AS NVARCHAR(128)),
       CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS NVARCHAR(512)),
       CONVERT(VARCHAR(16), SYSDATETIME(), 126), DATEPART(TZOFFSET, SYSDATETIMEOFFSET()),
       (SELECT TOP (1) status_desc FROM sys.dm_server_services
        WHERE servicename LIKE N'SQL Server Agent%'),
       CASE WHEN rs.last_log_backup_lsn IS NULL THEN 0 ELSE 1 END
FROM sys.databases AS d
JOIN sys.database_recovery_status AS rs ON rs.database_id = d.database_id
WHERE d.database_id = DB_ID()
"""

CONSULTA_BACKUPS_PROGRAMADOS = """
SELECT j.name, CONVERT(VARCHAR(16), a.next_scheduled_run_date, 126)
FROM msdb.dbo.sysjobs AS j
LEFT JOIN msdb.dbo.sysjobactivity AS a
    ON a.job_id = j.job_id
   AND a.session_id = (SELECT MAX(session_id) FROM msdb.dbo.syssessions)
WHERE j.name LIKE ? ESCAPE '\\'
ORDER BY j.name
"""

CONSULTA_QUERY_STORE = """
SELECT actual_state_desc, desired_state_desc, readonly_reason,
       current_storage_size_mb, max_storage_size_mb, query_capture_mode_desc
FROM sys.database_query_store_options
"""

CONSULTA_PLAN_CACHE = """
SELECT COUNT(*), COALESCE(SUM(CAST(cp.size_in_bytes AS BIGINT)), 0) / 1048576
FROM sys.dm_exec_cached_plans AS cp
CROSS APPLY sys.dm_exec_plan_attributes(cp.plan_handle) AS pa
WHERE pa.attribute = 'dbid' AND CAST(pa.value AS INT) = DB_ID()
"""


def bloqueadores(conexion: pyodbc.Connection) -> list[dict]:
    return [
        {
            "sid": f[0],
            "login": f[1],
            "host": f[2],
            "programa": f[3],
            "base": f[4],
            "bloquea_a": f[5],
            "espera_s": round((f[6] or 0) / 1000, 1),
            "raiz": bool(f[7]),
            "consulta": (f[8] or "").strip()[:500],
        }
        for f in conexion.cursor().execute(CONSULTA_BLOQUEADORES).fetchall()
    ]


def diagnosticar(integrado: Integrado, conexion: pyodbc.Connection | None) -> dict:
    """Información previa a la ejecución. ``conexion`` apunta a la base elegida."""
    if integrado.id == "kill_bloqueos":
        return {"bloqueadores": bloqueadores(conexion)}
    if integrado.id == "shrink_log":
        fila = conexion.cursor().execute(CONSULTA_LOG).fetchone()
        return {
            "recuperacion": fila[0],
            "espera_reutilizacion": fila[1],
            "archivo": fila[2],
            "tamano_mb": fila[3],
            "usado_mb": int(fila[4]),
            "uso": round(fila[5], 1),
        }
    if integrado.id == "rebuild_indices":
        filas = conexion.cursor().execute(CONSULTA_FRAGMENTACION).fetchall()
        return {
            "indices": [
                {
                    "esquema": f[0],
                    "tabla": f[1],
                    "indice": f[2],
                    "fragmentacion": round(f[3], 1),
                    "paginas": f[4],
                }
                for f in filas
            ],
            "online": bool(filas) and int(filas[0][5]) in EDICIONES_ONLINE,
        }
    if integrado.id == "update_statistics":
        return {
            "estadisticas": [
                {
                    "esquema": f[0],
                    "tabla": f[1],
                    "estadistica": f[2],
                    "filas": f[3],
                    "modificaciones": f[4],
                    "actualizada": f[5].isoformat() if f[5] else None,
                }
                for f in conexion.cursor().execute(CONSULTA_ESTADISTICAS).fetchall()
            ]
        }
    if integrado.id == "limpiar_plan_cache":
        planes, mb = conexion.cursor().execute(CONSULTA_PLAN_CACHE).fetchone()
        return {"planes": planes, "tamano_mb": mb}
    if integrado.id in ("programar_backup_full", "programar_backup_log"):
        return diagnostico_backup(conexion, integrado.id)
    if integrado.id == "reintentar_job":
        jobs = monitor.leer_jobs(conexion.cursor())
        return {
            "jobs": [
                {**j, "estado": estado_job(j)}
                for j in jobs
                if j["habilitado"] and j["corriendo_s"] is None
            ]
        }
    if integrado.id == "activar_query_store":
        cursor = conexion.cursor()
        version = int(cursor.execute("SELECT SERVERPROPERTY('ProductMajorVersion')").fetchone()[0])
        if version < VERSION_QUERY_STORE:
            return {"version": version, "disponible": False}
        fila = cursor.execute(CONSULTA_QUERY_STORE).fetchone()
        return {
            "version": version,
            "disponible": True,
            "estado": fila[0],
            "deseado": fila[1],
            "motivo_solo_lectura": fila[2],
            "usado_mb": int(fila[3] or 0),
            "maximo_mb": int(fila[4] or 0),
            "captura": fila[5],
        }
    if integrado.id == "base_online":
        filas = conexion.cursor().execute(CONSULTA_BASES_NO_DISPONIBLES).fetchall()
        return {
            "bases": [
                {"nombre": f[0], "estado": f[1], "recuperable": f[1] == ESTADO_RECUPERABLE}
                for f in filas
            ]
        }
    return {}


def _patron_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("[", "\\[")


def diagnostico_backup(conexion: pyodbc.Connection, workaround_id: str) -> dict:
    cursor = conexion.cursor()
    fila = cursor.execute(CONSULTA_BACKUP).fetchone()
    base = cursor.execute("SELECT DB_NAME()").fetchone()[0]
    edicion = int(fila[1])
    tipo = "full" if workaround_id == "programar_backup_full" else "log"
    prefijo = _patron_like(f"{PREFIJO_JOB_BACKUP} {tipo} · {base} · ") + "%"
    return {
        "base": base,
        "recuperacion": fila[0],
        # Express no tiene Agent; la compresión no está en Express ni en Web.
        "agente": None if edicion == EDICION_EXPRESS else fila[6],
        "compresion": edicion != EDICION_EXPRESS and "Web" not in (fila[2] or ""),
        "carpeta": fila[3],
        "ahora_servidor": fila[4],
        "desfase_min": int(fila[5]),
        "cadena_log": bool(fila[7]),
        "programados": [
            {"nombre": f[0], "proxima": f[1]}
            for f in cursor.execute(CONSULTA_BACKUPS_PROGRAMADOS, prefijo).fetchall()
        ],
    }


def _scripts_servicios(nombres: str) -> str:
    return (
        f"Get-Service -Name {nombres} -ErrorAction SilentlyContinue | "
        "Select-Object Name, @{n='Status';e={$_.Status.ToString()}} | ConvertTo-Json -Compress"
    )


SCRIPT_ESTADO_SERVICIOS = _scripts_servicios("'MSSQLSERVER','MSSQL$*'")
SCRIPT_ESTADO_AGENTE = _scripts_servicios("'SQLSERVERAGENT','SQLAgent$*'")
# Workarounds que inician un servicio de Windows por WinRM: script de diagnóstico y nombres
# de servicio admitidos.
SERVICIOS_WINRM = {
    "iniciar_servicio": (SCRIPT_ESTADO_SERVICIOS, PATRON_SERVICIO),
    "iniciar_agente": (SCRIPT_ESTADO_AGENTE, PATRON_AGENTE),
}


def servicios_desde_json(salida: str) -> list[dict]:
    datos = json.loads(salida) if salida.strip() else []
    if isinstance(datos, dict):
        datos = [datos]
    return [{"nombre": d["Name"], "estado": d["Status"]} for d in datos]


# --- Pasos --------------------------------------------------------------------------


def pasos(
    integrado: Integrado, base: str | None, diagnostico: dict, parametros: dict
) -> list[Paso]:
    """Pasos a ejecutar. Valida lo elegido contra el diagnóstico recién tomado."""
    if integrado.id == "kill_bloqueos":
        vigentes = {b["sid"] for b in diagnostico["bloqueadores"]}
        elegidos = parametros.get("sids") or []
        if not elegidos or any(not isinstance(s, int) or s not in vigentes for s in elegidos):
            # La lista cambió desde que se mostró: se pide volver a revisarla.
            raise ErrorDominio("WKR_EJE_001")
        return [Paso(f"KILL {sid}", sql=f"KILL {int(sid)}") for sid in elegidos]

    if integrado.id == "shrink_log":
        objetivo = max(math.ceil(diagnostico["usado_mb"] * MARGEN_LOG), MINIMO_LOG_MB)
        return [
            Paso("CHECKPOINT", sql="CHECKPOINT"),
            Paso(
                f"DBCC SHRINKFILE {diagnostico['archivo']} → {objetivo} MB",
                sql=f"DBCC SHRINKFILE ({literal(diagnostico['archivo'])}, {objetivo})",
            ),
        ]

    if integrado.id == "rebuild_indices":
        online = "ON" if diagnostico["online"] else "OFF"
        return [
            Paso(
                f"REBUILD {i['esquema']}.{i['tabla']}.{i['indice']} ({i['fragmentacion']}%)",
                sql=(
                    f"ALTER INDEX {citar(i['indice'])} ON {citar(i['esquema'])}."
                    f"{citar(i['tabla'])} REBUILD WITH (ONLINE = {online})"
                ),
            )
            for i in diagnostico["indices"]
        ]

    if integrado.id == "update_statistics":
        return [
            Paso(
                f"UPDATE STATISTICS {e['esquema']}.{e['tabla']} {e['estadistica']}",
                sql=(
                    f"UPDATE STATISTICS {citar(e['esquema'])}.{citar(e['tabla'])} "
                    f"{citar(e['estadistica'])}"
                ),
            )
            for e in diagnostico["estadisticas"]
        ]

    if integrado.id == "limpiar_plan_cache":
        return [
            Paso(
                "CLEAR PROCEDURE_CACHE",
                sql="ALTER DATABASE SCOPED CONFIGURATION CLEAR PROCEDURE_CACHE",
            )
        ]

    if integrado.id == "checkpoint":
        return [Paso("CHECKPOINT", sql="CHECKPOINT")]

    if integrado.id == "base_online":
        elegida = parametros.get("base") or ""
        recuperables = {b["nombre"] for b in diagnostico["bases"] if b["recuperable"]}
        if elegida not in recuperables:
            raise ErrorDominio("WKR_EJE_001")
        return [
            Paso(
                f"ALTER DATABASE {elegida} SET ONLINE",
                sql=f"ALTER DATABASE {citar(elegida)} SET ONLINE",
            )
        ]

    if integrado.id in ("programar_backup_full", "programar_backup_log"):
        return [paso_backup(integrado.id, base, diagnostico, parametros)]

    if integrado.id == "reintentar_job":
        job_id = parametros.get("job_id") or ""
        jobs = {j["id"].upper(): j for j in diagnostico["jobs"]}
        if not PATRON_JOB.match(job_id) or job_id.upper() not in jobs:
            raise ErrorDominio("WKR_EJE_001")
        job = jobs[job_id.upper()]
        return [
            Paso(
                f"sp_start_job {job['nombre']}",
                sql=f"EXEC msdb.dbo.sp_start_job @job_id = '{job_id.upper()}'",
            )
        ]

    if integrado.id == "activar_query_store":
        if not diagnostico["disponible"]:
            raise ErrorDominio("WKR_EJE_006")
        if diagnostico["estado"] == "READ_WRITE" and diagnostico["deseado"] == "READ_WRITE":
            raise ErrorDominio("WKR_EJE_002")
        # Si quedó en solo lectura por falta de espacio, se le da el doble.
        maximo = max(diagnostico["maximo_mb"], QUERY_STORE_MB_MINIMO)
        if diagnostico["usado_mb"] >= maximo * 0.9:
            maximo = min(maximo * 2, QUERY_STORE_MB_MAXIMO)
        return [
            Paso(
                f"QUERY_STORE = ON ({maximo} MB, {QUERY_STORE_DIAS} d)",
                sql=(
                    f"ALTER DATABASE {citar(base)} SET QUERY_STORE = ON ("
                    "OPERATION_MODE = READ_WRITE, QUERY_CAPTURE_MODE = AUTO, "
                    f"MAX_STORAGE_SIZE_MB = {maximo}, "
                    f"CLEANUP_POLICY = (STALE_QUERY_THRESHOLD_DAYS = {QUERY_STORE_DIAS}), "
                    "SIZE_BASED_CLEANUP_MODE = AUTO)"
                ),
            )
        ]

    # iniciar_servicio o iniciar_agente
    patron = SERVICIOS_WINRM[integrado.id][1]
    servicio = parametros.get("servicio") or ""
    detenidos = {s["nombre"] for s in diagnostico.get("servicios", []) if s["estado"] != "Running"}
    if not patron.match(servicio) or servicio not in detenidos:
        raise ErrorDominio("WKR_EJE_001")
    return [
        Paso(
            f"Start-Service {servicio}",
            powershell=(
                f"Start-Service -Name '{servicio}' -ErrorAction Stop; "
                f"(Get-Service -Name '{servicio}').WaitForStatus('Running', '00:01:00'); "
                f"(Get-Service -Name '{servicio}').Status.ToString()"
            ),
        )
    ]


def _nombre_archivo(texto: str) -> str:
    """Nombre de base apto para un archivo en Windows y Linux."""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", texto)


def paso_backup(workaround_id: str, base: str, diagnostico: dict, parametros: dict) -> Paso:
    """Crea un job de una sola ejecución que se borra al terminar bien; si falla, queda
    en la instancia con el error a la vista (y lo informan las alertas de jobs)."""
    if not diagnostico["agente"]:
        raise ErrorDominio("WKR_EJE_003")
    full = workaround_id == "programar_backup_full"
    if not full and (
        diagnostico["recuperacion"] not in RECUPERACION_CON_LOG or not diagnostico["cadena_log"]
    ):
        raise ErrorDominio("WKR_EJE_005")
    try:
        inicio = datetime.strptime(parametros.get("inicio") or "", FORMATO_INICIO)
    except ValueError:
        raise ErrorDominio("WKR_EJE_004") from None
    ahora = datetime.strptime(diagnostico["ahora_servidor"], FORMATO_INICIO)
    if not ahora < inicio <= ahora + timedelta(days=MAXIMO_DIAS_PROGRAMACION):
        raise ErrorDominio("WKR_EJE_004")

    tipo = "full" if full else "log"
    marca = f"{inicio:%Y%m%d_%H%M}"
    if full:
        # COPY_ONLY: no altera la cadena de backups que ya tenga la empresa.
        archivo = f"{_nombre_archivo(base)}_FULL_COPY_{marca}.bak"
        comando = f"BACKUP DATABASE {citar(base)} TO DISK = N'{archivo}' WITH COPY_ONLY, "
    else:
        # El de log no puede ser COPY_ONLY: no liberaría el log. Queda registrado en msdb.
        archivo = f"{_nombre_archivo(base)}_LOG_{marca}.trn"
        comando = f"BACKUP LOG {citar(base)} TO DISK = N'{archivo}' WITH "
    comando += "CHECKSUM" + (", COMPRESSION" if diagnostico["compresion"] else "") + ", STATS = 10"
    nombre_job = f"{PREFIJO_JOB_BACKUP} {tipo} · {base} · {inicio:%Y-%m-%d %H:%M}"
    sql = (
        "DECLARE @job UNIQUEIDENTIFIER;\n"
        f"EXEC msdb.dbo.sp_add_job @job_name = {literal(nombre_job)}, @delete_level = 1, "
        f"@description = {literal('Programado desde DBA Assistant.')}, @job_id = @job OUTPUT;\n"
        "EXEC msdb.dbo.sp_add_jobstep @job_id = @job, @step_name = N'Backup', "
        f"@subsystem = N'TSQL', @database_name = N'master', @command = {literal(comando)};\n"
        "EXEC msdb.dbo.sp_add_jobschedule @job_id = @job, @name = N'Una vez', @freq_type = 1, "
        f"@active_start_date = {inicio:%Y%m%d}, @active_start_time = {inicio:%H%M}00;\n"
        "EXEC msdb.dbo.sp_add_jobserver @job_id = @job;"
    )
    return Paso(f"{nombre_job} → {archivo}", sql=sql)


def sugerido_para(clave: str) -> tuple[str, dict] | None:
    """Workaround sugerido para la condición de una alerta, con sus parámetros."""
    if clave == "evento:bloqueo":
        return "kill_bloqueos", {}
    if clave.startswith("evento:log:"):
        return "shrink_log", {"base": clave.removeprefix("evento:log:")}
    if clave == "evento:instancia":
        return "iniciar_servicio", {}
    if clave.startswith("evento:base:"):
        return "base_online", {"base": clave.removeprefix("evento:base:")}
    if clave.startswith("evento:backup_full:"):
        return "programar_backup_full", {"base": clave.removeprefix("evento:backup_full:")}
    if clave.startswith("evento:backup_log:"):
        return "programar_backup_log", {"base": clave.removeprefix("evento:backup_log:")}
    if clave.startswith("evento:job:"):
        return "reintentar_job", {"job_id": clave.removeprefix("evento:job:")}
    if clave == "evento:agente":
        return "iniciar_agente", {}
    return None
