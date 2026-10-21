"""Suscripciones push por dispositivo, en la base SQL Server del tenant."""

from dataclasses import dataclass

from app.datos import sqlserver


@dataclass(frozen=True)
class Suscripcion:
    endpoint: str
    p256dh: str
    auth: str
    idioma: str | None = None  # el del usuario dueño del dispositivo


def guardar(base: str, tenant_id: str, usuario_id: str, s: Suscripcion, agente: str | None) -> None:
    """Alta o reasignación: un mismo navegador puede cambiar de usuario."""
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        cursor.execute("DELETE FROM dbo.suscripcion_push WHERE endpoint = ?", s.endpoint)
        cursor.execute(
            """INSERT INTO dbo.suscripcion_push
                   (tenant_id, usuario_id, endpoint, p256dh, auth, agente)
               VALUES (?, ?, ?, ?, ?, ?)""",
            tenant_id,
            usuario_id,
            s.endpoint,
            s.p256dh,
            s.auth,
            (agente or "")[:300],
        )
        conexion.commit()


def eliminar(base: str, tenant_id: str, endpoint: str) -> None:
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            "DELETE FROM dbo.suscripcion_push WHERE tenant_id = ? AND endpoint = ?",
            tenant_id,
            endpoint,
        )
        conexion.commit()


def listar_por_roles(base: str, tenant_id: str, roles: tuple[str, ...]) -> list[Suscripcion]:
    """Dispositivos de los usuarios habilitados que tienen alguno de los roles."""
    if not roles:
        return []
    marcadores = ", ".join("?" for _ in roles)
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""SELECT s.endpoint, s.p256dh, s.auth, u.idioma
                    FROM dbo.suscripcion_push AS s
                    JOIN dbo.usuario AS u ON u.id = s.usuario_id
                    WHERE s.tenant_id = ? AND u.estado IN ('invitado', 'activo')
                      AND u.fecha_baja IS NULL AND u.rol IN ({marcadores})""",
                tenant_id,
                *roles,
            )
            .fetchall()
        )
    return [Suscripcion(f.endpoint, f.p256dh, f.auth, f.idioma) for f in filas]


def contar_del_usuario(base: str, tenant_id: str, usuario_id: str) -> int:
    with sqlserver.conectar(base) as conexion:
        return (
            conexion.cursor()
            .execute(
                "SELECT COUNT(*) FROM dbo.suscripcion_push WHERE tenant_id = ? AND usuario_id = ?",
                tenant_id,
                usuario_id,
            )
            .fetchone()[0]
        )
