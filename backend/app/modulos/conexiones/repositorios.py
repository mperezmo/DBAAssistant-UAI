"""Persistencia de conexiones en la base SQL Server del tenant y lecturas básicas de cada
instancia registrada (versión y bases)."""

from app.datos import sqlserver
from app.modulos.conexiones.entidades import Conexion

COLUMNAS = (
    "id, tenant_id, alias, host, puerto, instancia, ambiente, usuario_sql,"
    " password_sql_cifrada, winrm_usuario, winrm_password_cifrada, winrm_puerto, winrm_https,"
    " ventana_inicio, ventana_fin, zona_horaria, sandbox_sincronizacion"
)


def _a_entidad(fila) -> Conexion:
    return Conexion(
        id=str(fila.id),
        tenant_id=str(fila.tenant_id),
        alias=fila.alias,
        host=fila.host,
        puerto=fila.puerto,
        instancia=fila.instancia,
        ambiente=fila.ambiente,
        usuario_sql=fila.usuario_sql,
        password_sql_cifrada=fila.password_sql_cifrada,
        winrm_usuario=fila.winrm_usuario,
        winrm_password_cifrada=fila.winrm_password_cifrada,
        winrm_puerto=fila.winrm_puerto,
        winrm_https=bool(fila.winrm_https),
        ventana_inicio=fila.ventana_inicio,
        ventana_fin=fila.ventana_fin,
        zona_horaria=fila.zona_horaria,
        sandbox_sincronizacion=fila.sandbox_sincronizacion,
    )


def listar(base: str, tenant_id: str) -> list[Conexion]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS} FROM dbo.conexion WHERE tenant_id = ?
                    ORDER BY CASE ambiente WHEN 'PROD' THEN 0 WHEN 'TESTING' THEN 1 ELSE 2 END,
                             alias""",
                tenant_id,
            )
            .fetchall()
        )
    return [_a_entidad(fila) for fila in filas]


def obtener(base: str, tenant_id: str, conexion_id: str) -> Conexion | None:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"SELECT {COLUMNAS} FROM dbo.conexion WHERE tenant_id = ? AND id = ?",
                tenant_id,
                conexion_id,
            )
            .fetchone()
        )
    return _a_entidad(fila) if fila else None


def existe_alias(base: str, tenant_id: str, alias: str, excluir_id: str | None = None) -> bool:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                """SELECT 1 FROM dbo.conexion
                   WHERE tenant_id = ? AND alias = ? AND (? IS NULL OR id <> ?)""",
                tenant_id,
                alias,
                excluir_id,
                excluir_id,
            )
            .fetchone()
        )
    return fila is not None


def guardar(base: str, c: Conexion, nueva: bool) -> str:
    valores = (
        c.alias,
        c.host,
        c.puerto,
        c.instancia,
        c.ambiente,
        c.usuario_sql,
        c.password_sql_cifrada,
        c.winrm_usuario,
        c.winrm_password_cifrada,
        c.winrm_puerto,
        c.winrm_https,
        c.ventana_inicio,
        c.ventana_fin,
        c.zona_horaria,
        c.sandbox_sincronizacion,
    )
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        if nueva:
            conexion_id = cursor.execute(
                """INSERT INTO dbo.conexion (alias, host, puerto, instancia, ambiente,
                       usuario_sql, password_sql_cifrada, winrm_usuario,
                       winrm_password_cifrada, winrm_puerto, winrm_https, ventana_inicio,
                       ventana_fin, zona_horaria, sandbox_sincronizacion, tenant_id)
                   OUTPUT inserted.id
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                *valores,
                c.tenant_id,
            ).fetchone()[0]
        else:
            conexion_id = c.id
            cursor.execute(
                """UPDATE dbo.conexion
                   SET alias = ?, host = ?, puerto = ?, instancia = ?, ambiente = ?,
                       usuario_sql = ?, password_sql_cifrada = ?, winrm_usuario = ?,
                       winrm_password_cifrada = ?, winrm_puerto = ?, winrm_https = ?,
                       ventana_inicio = ?, ventana_fin = ?, zona_horaria = ?,
                       sandbox_sincronizacion = ?,
                       actualizado_en = SYSUTCDATETIME()
                   WHERE tenant_id = ? AND id = ?""",
                *valores,
                c.tenant_id,
                c.id,
            )
        conexion.commit()
    return str(conexion_id)


def eliminar(base: str, tenant_id: str, conexion_id: str) -> None:
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            "DELETE FROM dbo.conexion WHERE tenant_id = ? AND id = ?", tenant_id, conexion_id
        )
        conexion.commit()


# --- Lecturas de la instancia registrada --------------------------------------------

CONSULTA_PRUEBA = """
    SELECT CAST(SERVERPROPERTY('ProductVersion') AS NVARCHAR(128)),
           CAST(SERVERPROPERTY('Edition') AS NVARCHAR(128)),
           (SELECT COUNT(*) FROM sys.databases WHERE database_id > 4),
           HAS_PERMS_BY_NAME(NULL, NULL, 'VIEW SERVER STATE'),
           HAS_PERMS_BY_NAME('msdb.dbo.backupset', 'OBJECT', 'SELECT'),
           HAS_PERMS_BY_NAME('msdb.dbo.sysjobs', 'OBJECT', 'SELECT'),
           HAS_PERMS_BY_NAME('msdb.dbo.sp_add_job', 'OBJECT', 'EXECUTE')
"""
# Permisos que usa la aplicación, en el orden de la consulta de prueba: monitoreo y Query
# Store; historial de backups; jobs del SQL Server Agent; backups programados y reintentos.
PERMISOS = ("view_server_state", "msdb_backups", "msdb_jobs", "agente_crear")


def datos_de_instancia(
    servidor: str, usuario: str, password: str
) -> tuple[str, str, int, tuple[str, ...]]:
    """Versión, edición, cantidad de bases de usuario y permisos que le faltan al login."""
    with sqlserver.conectar_externo(servidor, usuario, password) as abierta:
        fila = abierta.cursor().execute(CONSULTA_PRUEBA).fetchone()
    faltantes = tuple(p for p, tiene in zip(PERMISOS, fila[3:], strict=True) if not tiene)
    return fila[0], fila[1], fila[2], faltantes


def bases_en_linea(servidor: str, usuario: str, password: str, solo_de_usuario: bool) -> list[str]:
    """Bases ONLINE de la instancia; con ``solo_de_usuario`` se omiten las de sistema."""
    filtro = "database_id > 4 AND " if solo_de_usuario else ""
    with sqlserver.conectar_externo(servidor, usuario, password) as abierta:
        filas = abierta.cursor().execute(
            f"SELECT name FROM sys.databases WHERE {filtro}state_desc = 'ONLINE' ORDER BY name"
        )
        return [f[0] for f in filas.fetchall()]
