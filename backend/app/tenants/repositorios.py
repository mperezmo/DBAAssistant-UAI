"""Acceso al catálogo de tenants en la base central de SQL Server."""

from uuid import UUID

from app.core.config import obtener_configuracion
from app.datos import sqlserver
from app.tenants.entidades import Tenant

CAMPOS = (
    "id",
    "slug",
    "nombre",
    "base_sqlserver",
    "base_mongodb",
    "estado",
    "auth0_organizacion_id",
)
COLUMNAS = ", ".join(CAMPOS)
COLUMNAS_INSERTADAS = ", ".join(f"inserted.{campo}" for campo in CAMPOS)


def _base() -> str:
    return obtener_configuracion().sqlserver_base_catalogo


def _a_entidad(fila) -> Tenant:
    return Tenant(
        id=UUID(str(fila.id)),
        slug=fila.slug,
        nombre=fila.nombre,
        base_sqlserver=fila.base_sqlserver,
        base_mongodb=fila.base_mongodb,
        estado=fila.estado,
        auth0_organizacion_id=fila.auth0_organizacion_id,
    )


def obtener_por_slug(slug: str) -> Tenant | None:
    with sqlserver.conectar(_base()) as conexion:
        fila = (
            conexion.cursor()
            .execute(f"SELECT {COLUMNAS} FROM dbo.tenant WHERE slug = ?", slug)
            .fetchone()
        )
    return _a_entidad(fila) if fila else None


def listar() -> list[Tenant]:
    with sqlserver.conectar(_base()) as conexion:
        filas = conexion.cursor().execute(f"SELECT {COLUMNAS} FROM dbo.tenant ORDER BY slug")
        return [_a_entidad(fila) for fila in filas.fetchall()]


def crear(slug: str, nombre: str, base_sqlserver: str, base_mongodb: str) -> Tenant:
    with sqlserver.conectar(_base()) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"""INSERT INTO dbo.tenant (slug, nombre, base_sqlserver, base_mongodb)
                OUTPUT {COLUMNAS_INSERTADAS}
                VALUES (?, ?, ?, ?)""",
                slug,
                nombre,
                base_sqlserver,
                base_mongodb,
            )
            .fetchone()
        )
        conexion.commit()
    return _a_entidad(fila)


def actualizar_estado(tenant_id: UUID, estado: str, detalle_error: str | None = None) -> None:
    with sqlserver.conectar(_base()) as conexion:
        conexion.cursor().execute(
            """UPDATE dbo.tenant
               SET estado = ?, detalle_error = ?, actualizado_en = SYSUTCDATETIME()
               WHERE id = ?""",
            estado,
            detalle_error,
            str(tenant_id),
        )
        conexion.commit()


def actualizar_organizacion(tenant_id: UUID, organizacion_id: str) -> None:
    with sqlserver.conectar(_base()) as conexion:
        conexion.cursor().execute(
            """UPDATE dbo.tenant
               SET auth0_organizacion_id = ?, actualizado_en = SYSUTCDATETIME()
               WHERE id = ?""",
            organizacion_id,
            str(tenant_id),
        )
        conexion.commit()


def crear_administrador(tenant: Tenant, email: str) -> None:
    """Registra en la base del tenant a su primer Administrador (estado invitado).

    Es idempotente: si el email ya existe no hace nada.
    """
    with sqlserver.conectar(tenant.base_sqlserver) as conexion:
        cursor = conexion.cursor()
        existe = cursor.execute(
            "SELECT 1 FROM dbo.usuario WHERE tenant_id = ? AND email = ?", str(tenant.id), email
        ).fetchone()
        if existe:
            return
        usuario_id = cursor.execute(
            """INSERT INTO dbo.usuario (tenant_id, email, rol)
               OUTPUT inserted.id VALUES (?, ?, 'ADMINISTRADOR')""",
            str(tenant.id),
            email,
        ).fetchone()[0]
        # El Administrador del sistema opera en todos los ambientes (TFI 10.4.2.3).
        cursor.executemany(
            "INSERT INTO dbo.usuario_ambiente (usuario_id, tenant_id, ambiente) VALUES (?, ?, ?)",
            [(usuario_id, str(tenant.id), ambiente) for ambiente in ("DEV", "TESTING", "PROD")],
        )
        conexion.commit()
