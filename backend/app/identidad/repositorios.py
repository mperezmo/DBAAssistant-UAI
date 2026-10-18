"""Usuarios del tenant en su base SQL Server."""

from app.datos import sqlserver
from app.identidad.entidades import DIAS_GRACIA_BAJA, Usuario

COLUMNAS = (
    "u.id, u.tenant_id, u.email, u.nombre, u.rol, u.estado, u.auth0_usuario_id,"
    " u.fecha_baja, u.ultimo_ingreso,"
    " (SELECT STRING_AGG(a.ambiente, ',') FROM dbo.usuario_ambiente a"
    "  WHERE a.usuario_id = u.id) AS ambientes"
)
ORDEN_AMBIENTES = {"DEV": 0, "TESTING": 1, "PROD": 2}


def _a_entidad(fila) -> Usuario:
    ambientes = sorted(
        (fila.ambientes or "").split(",") if fila.ambientes else [], key=ORDEN_AMBIENTES.get
    )
    return Usuario(
        id=str(fila.id),
        tenant_id=str(fila.tenant_id),
        email=fila.email,
        nombre=fila.nombre,
        rol=fila.rol,
        estado=fila.estado,
        auth0_usuario_id=fila.auth0_usuario_id,
        ambientes=tuple(ambientes),
        fecha_baja=fila.fecha_baja,
        ultimo_ingreso=fila.ultimo_ingreso,
    )


def buscar_para_ingreso(base: str, tenant_id: str, auth0_id: str, email: str) -> Usuario | None:
    """Busca al usuario por su identidad de Auth0 o, si aún no la tiene, por email."""
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"""SELECT TOP 1 {COLUMNAS} FROM dbo.usuario u
                    WHERE u.tenant_id = ?
                      AND (u.auth0_usuario_id = ? OR (u.auth0_usuario_id IS NULL AND u.email = ?))
                    ORDER BY CASE WHEN u.auth0_usuario_id = ? THEN 0 ELSE 1 END""",
                tenant_id,
                auth0_id,
                email,
                auth0_id,
            )
            .fetchone()
        )
    return _a_entidad(fila) if fila else None


def activar(base: str, usuario_id: str, auth0_id: str, nombre: str | None) -> None:
    """Vincula la identidad de Auth0 en el primer ingreso y deja al usuario activo."""
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            """UPDATE dbo.usuario
               SET auth0_usuario_id = ?, nombre = COALESCE(nombre, ?), estado = 'activo',
                   actualizado_en = SYSUTCDATETIME()
               WHERE id = ? AND estado = 'invitado'""",
            auth0_id,
            nombre,
            usuario_id,
        )
        conexion.commit()


def registrar_ingreso(base: str, usuario_id: str) -> None:
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            "UPDATE dbo.usuario SET ultimo_ingreso = SYSUTCDATETIME() WHERE id = ?", usuario_id
        )
        conexion.commit()


def obtener(base: str, tenant_id: str, usuario_id: str) -> Usuario | None:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"SELECT {COLUMNAS} FROM dbo.usuario u WHERE u.tenant_id = ? AND u.id = ?",
                tenant_id,
                usuario_id,
            )
            .fetchone()
        )
    return _a_entidad(fila) if fila else None


def obtener_por_email(base: str, tenant_id: str, email: str) -> Usuario | None:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"SELECT {COLUMNAS} FROM dbo.usuario u WHERE u.tenant_id = ? AND u.email = ?",
                tenant_id,
                email,
            )
            .fetchone()
        )
    return _a_entidad(fila) if fila else None


def listar(base: str, tenant_id: str, bajas: bool) -> list[Usuario]:
    """Usuarios vigentes o, con ``bajas``, solo los dados de baja."""
    condicion = "u.estado = 'baja'" if bajas else "u.estado <> 'baja'"
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS} FROM dbo.usuario u
                    WHERE u.tenant_id = ? AND {condicion}
                    ORDER BY u.email""",
                tenant_id,
            )
            .fetchall()
        )
    return [_a_entidad(fila) for fila in filas]


def _reemplazar_ambientes(cursor, tenant_id: str, usuario_id: str, ambientes) -> None:
    cursor.execute("DELETE FROM dbo.usuario_ambiente WHERE usuario_id = ?", usuario_id)
    if ambientes:
        cursor.executemany(
            "INSERT INTO dbo.usuario_ambiente (usuario_id, tenant_id, ambiente) VALUES (?, ?, ?)",
            [(usuario_id, tenant_id, ambiente) for ambiente in ambientes],
        )


def crear(base: str, tenant_id: str, email: str, nombre: str | None, rol: str, ambientes) -> str:
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        usuario_id = str(
            cursor.execute(
                """INSERT INTO dbo.usuario (tenant_id, email, nombre, rol)
                   OUTPUT inserted.id VALUES (?, ?, ?, ?)""",
                tenant_id,
                email,
                nombre,
                rol,
            ).fetchone()[0]
        )
        _reemplazar_ambientes(cursor, tenant_id, usuario_id, ambientes)
        conexion.commit()
    return usuario_id


def actualizar(
    base: str, tenant_id: str, usuario_id: str, nombre: str | None, rol: str, ambientes
) -> None:
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """UPDATE dbo.usuario SET nombre = ?, rol = ?, actualizado_en = SYSUTCDATETIME()
               WHERE tenant_id = ? AND id = ?""",
            nombre,
            rol,
            tenant_id,
            usuario_id,
        )
        _reemplazar_ambientes(cursor, tenant_id, usuario_id, ambientes)
        conexion.commit()


def dar_baja(base: str, tenant_id: str, usuario_id: str) -> None:
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            """UPDATE dbo.usuario
               SET estado_previo = estado, estado = 'baja', fecha_baja = SYSUTCDATETIME(),
                   actualizado_en = SYSUTCDATETIME()
               WHERE tenant_id = ? AND id = ? AND estado <> 'baja'""",
            tenant_id,
            usuario_id,
        )
        conexion.commit()


def rehabilitar(base: str, tenant_id: str, usuario_id: str) -> None:
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            """UPDATE dbo.usuario
               SET estado = COALESCE(estado_previo, 'invitado'), estado_previo = NULL,
                   fecha_baja = NULL, actualizado_en = SYSUTCDATETIME()
               WHERE tenant_id = ? AND id = ? AND estado = 'baja'""",
            tenant_id,
            usuario_id,
        )
        conexion.commit()


def purgar_bajas(base: str, tenant_id: str) -> list[str]:
    """Elimina los usuarios con baja de más de 30 días y devuelve sus emails."""
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """DELETE FROM dbo.usuario
                   OUTPUT deleted.email
                   WHERE tenant_id = ? AND estado = 'baja'
                     AND fecha_baja < DATEADD(DAY, -?, SYSUTCDATETIME())""",
                tenant_id,
                DIAS_GRACIA_BAJA,
            )
            .fetchall()
        )
        conexion.commit()
    return [fila.email for fila in filas]


def contar_administradores_vigentes(base: str, tenant_id: str) -> int:
    with sqlserver.conectar(base) as conexion:
        return (
            conexion.cursor()
            .execute(
                """SELECT COUNT(*) FROM dbo.usuario
                   WHERE tenant_id = ? AND rol = 'ADMINISTRADOR'
                     AND estado IN ('invitado', 'activo')""",
                tenant_id,
            )
            .fetchone()[0]
        )
