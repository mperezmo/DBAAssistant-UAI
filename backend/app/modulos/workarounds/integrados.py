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

import pyodbc

from app.core.errores import ErrorDominio
from app.identidad.entidades import AMBIENTES, ROLES_OPERATIVOS
from app.modulos.workarounds.entidades import Paso

TODOS = ROLES_OPERATIVOS


@dataclass(frozen=True)
class Integrado:
    id: str
    codigo: str
    nombre: str
    descripcion: str
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


CATALOGO: dict[str, Integrado] = {
    i.id: i
    for i in (
        Integrado(
            "kill_bloqueos",
            "WK-01",
            "Liberar sesiones bloqueantes",
            "Lista las sesiones que bloquean a otras y termina con KILL las que se elijan.",
            "performance",
            "critical",
            TODOS,
            requiere_base=False,
        ),
        Integrado(
            "shrink_log",
            "WK-02",
            "Shrink del log de transacciones",
            "Hace CHECKPOINT y reduce el log de la base al espacio en uso más un margen.",
            "espacio",
            "warning",
            TODOS,
            requiere_base=True,
            eventos_automaticos=("log",),
        ),
        Integrado(
            "rebuild_indices",
            "WK-03",
            "Reconstruir índices fragmentados",
            "REBUILD de los índices con fragmentación mayor al 30% y al menos 1000 páginas.",
            "performance",
            "warning",
            TODOS,
            requiere_base=True,
            requiere_ventana=True,
        ),
        Integrado(
            "update_statistics",
            "WK-04",
            "Actualizar estadísticas desactualizadas",
            "UPDATE STATISTICS sobre las estadísticas con más del 20% de filas modificadas.",
            "mantenimiento",
            "info",
            TODOS,
            requiere_base=True,
            requiere_ventana=True,
        ),
        Integrado(
            "limpiar_plan_cache",
            "WK-05",
            "Limpiar plan cache de la base",
            "Elimina los planes en caché de la base elegida (no afecta a las demás).",
            "performance",
            "warning",
            TODOS,
            requiere_base=True,
        ),
        Integrado(
            "checkpoint",
            "WK-06",
            "Forzar checkpoint",
            "Ejecuta CHECKPOINT en la base elegida para escribir a disco las páginas modificadas.",
            "mantenimiento",
            "info",
            TODOS,
            requiere_base=True,
            eventos_automaticos=("log",),
        ),
        Integrado(
            "iniciar_servicio",
            "WK-07",
            "Iniciar el servicio de SQL Server",
            "Inicia por WinRM el servicio del motor en el servidor Windows cuando está detenido.",
            "servicio",
            "critical",
            TODOS,
            requiere_base=False,
            eventos_automaticos=("instancia",),
        ),
        Integrado(
            "base_online",
            "WK-08",
            "Poner online una base",
            "Lista las bases que no están disponibles y pone ONLINE la elegida, si está OFFLINE.",
            "servicio",
            "critical",
            TODOS,
            requiere_base=False,
            eventos_automaticos=("base",),
        ),
    )
}

PATRON_SERVICIO = re.compile(r"^MSSQL(SERVER|\$[A-Za-z0-9_]{1,16})$")
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
    if integrado.id == "base_online":
        filas = conexion.cursor().execute(CONSULTA_BASES_NO_DISPONIBLES).fetchall()
        return {
            "bases": [
                {"nombre": f[0], "estado": f[1], "recuperable": f[1] == ESTADO_RECUPERABLE}
                for f in filas
            ]
        }
    return {}


SCRIPT_ESTADO_SERVICIOS = (
    "Get-Service -Name 'MSSQLSERVER','MSSQL$*' -ErrorAction SilentlyContinue | "
    "Select-Object Name, @{n='Status';e={$_.Status.ToString()}} | ConvertTo-Json -Compress"
)


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

    # iniciar_servicio
    servicio = parametros.get("servicio") or ""
    detenidos = {s["nombre"] for s in diagnostico.get("servicios", []) if s["estado"] != "Running"}
    if not PATRON_SERVICIO.match(servicio) or servicio not in detenidos:
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
    return None
