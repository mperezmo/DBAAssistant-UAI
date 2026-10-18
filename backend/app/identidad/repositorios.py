"""Usuarios del tenant en su base SQL Server."""

from app.datos import sqlserver
from app.identidad.entidades import Usuario

COLUMNAS = "id, tenant_id, email, nombre, rol, estado, auth0_usuario_id"


def _a_entidad(fila) -> Usuario:
    return Usuario(
        id=str(fila.id),
        tenant_id=str(fila.tenant_id),
        email=fila.email,
        nombre=fila.nombre,
        rol=fila.rol,
        estado=fila.estado,
        auth0_usuario_id=fila.auth0_usuario_id,
    )


def buscar_para_ingreso(base: str, tenant_id: str, auth0_id: str, email: str) -> Usuario | None:
    """Busca al usuario por su identidad de Auth0 o, si aún no la tiene, por email."""
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"""SELECT TOP 1 {COLUMNAS} FROM dbo.usuario
                    WHERE tenant_id = ?
                      AND (auth0_usuario_id = ? OR (auth0_usuario_id IS NULL AND email = ?))
                    ORDER BY CASE WHEN auth0_usuario_id = ? THEN 0 ELSE 1 END""",
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
