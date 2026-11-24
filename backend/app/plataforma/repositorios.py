"""Operadores, invitaciones y configuración de la plataforma en la base del catálogo."""

from datetime import datetime

from app.core.config import obtener_configuracion
from app.datos import sqlserver
from app.plataforma.entidades import Invitacion, Operador

COLUMNAS_OPERADOR = (
    "id, email, nombre, estado, auth0_usuario_id, creado_por, creado_en, ultimo_ingreso"
)
COLUMNAS_INVITACION = (
    "id, email, estado, creada_por, creada_en, vence_en, codigo_hash, codigo_vence_en, "
    "codigo_intentos, tenant_id, usada_en, revocada_en"
)


def _insertadas(columnas: str) -> str:
    return ", ".join("inserted." + c.strip() for c in columnas.split(","))


def _base() -> str:
    return obtener_configuracion().sqlserver_base_catalogo


def _operador(fila) -> Operador:
    return Operador(
        id=str(fila.id),
        email=fila.email,
        nombre=fila.nombre,
        estado=fila.estado,
        auth0_usuario_id=fila.auth0_usuario_id,
        creado_por=fila.creado_por,
        creado_en=fila.creado_en,
        ultimo_ingreso=fila.ultimo_ingreso,
    )


def _invitacion(fila) -> Invitacion:
    return Invitacion(
        id=str(fila.id),
        email=fila.email,
        estado=fila.estado,
        creada_por=fila.creada_por,
        creada_en=fila.creada_en,
        vence_en=fila.vence_en,
        codigo_hash=fila.codigo_hash,
        codigo_vence_en=fila.codigo_vence_en,
        codigo_intentos=fila.codigo_intentos,
        tenant_id=str(fila.tenant_id) if fila.tenant_id else None,
        usada_en=fila.usada_en,
        revocada_en=fila.revocada_en,
    )


def _ejecutar(sql: str, *parametros) -> int:
    """Ejecuta una modificación y devuelve las filas afectadas."""
    with sqlserver.conectar(_base()) as conexion:
        cursor = conexion.cursor().execute(sql, *parametros)
        afectadas = cursor.rowcount
        conexion.commit()
    return afectadas


# --- Configuración ------------------------------------------------------------------


def organizacion() -> str | None:
    """Organización de Auth0 de los operadores (la crea ``crear-operador``)."""
    with sqlserver.conectar(_base()) as conexion:
        fila = (
            conexion.cursor()
            .execute("SELECT auth0_organizacion_id FROM dbo.plataforma WHERE id = 1")
            .fetchone()
        )
    return fila.auth0_organizacion_id if fila else None


def guardar_organizacion(organizacion_id: str) -> None:
    _ejecutar("UPDATE dbo.plataforma SET auth0_organizacion_id = ? WHERE id = 1", organizacion_id)


# --- Operadores ---------------------------------------------------------------------


def listar_operadores() -> list[Operador]:
    with sqlserver.conectar(_base()) as conexion:
        filas = conexion.cursor().execute(
            f"""SELECT {COLUMNAS_OPERADOR} FROM dbo.operador
                WHERE estado <> 'baja' ORDER BY creado_en"""
        )
        return [_operador(f) for f in filas.fetchall()]


def obtener_operador(operador_id: str) -> Operador | None:
    with sqlserver.conectar(_base()) as conexion:
        fila = (
            conexion.cursor()
            .execute(f"SELECT {COLUMNAS_OPERADOR} FROM dbo.operador WHERE id = ?", operador_id)
            .fetchone()
        )
    return _operador(fila) if fila else None


def obtener_operador_por_email(email: str) -> Operador | None:
    with sqlserver.conectar(_base()) as conexion:
        fila = (
            conexion.cursor()
            .execute(f"SELECT {COLUMNAS_OPERADOR} FROM dbo.operador WHERE email = ?", email)
            .fetchone()
        )
    return _operador(fila) if fila else None


def buscar_para_ingreso(auth0_usuario_id: str, email: str) -> Operador | None:
    """Operador por su identidad de Auth0 o, en el primer ingreso, por su email."""
    with sqlserver.conectar(_base()) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"""SELECT TOP 1 {COLUMNAS_OPERADOR} FROM dbo.operador
                    WHERE auth0_usuario_id = ? OR (auth0_usuario_id IS NULL AND email = ?)
                    ORDER BY CASE WHEN auth0_usuario_id = ? THEN 0 ELSE 1 END""",
                auth0_usuario_id,
                email,
                auth0_usuario_id,
            )
            .fetchone()
        )
    return _operador(fila) if fila else None


def crear_operador(email: str, nombre: str | None, creado_por: str) -> Operador | None:
    """Da de alta un operador; uno dado de baja vuelve como invitado.

    Devuelve None si el email ya es de un operador con acceso.
    """
    with sqlserver.conectar(_base()) as conexion:
        cursor = conexion.cursor()
        fila = cursor.execute(
            f"""UPDATE dbo.operador
                SET estado = 'invitado', nombre = COALESCE(?, nombre), auth0_usuario_id = NULL,
                    creado_por = ?, creado_en = SYSUTCDATETIME(), baja_en = NULL
                OUTPUT {_insertadas(COLUMNAS_OPERADOR)}
                WHERE email = ? AND estado = 'baja'""",
            nombre,
            creado_por,
            email,
        ).fetchone()
        if fila is None:
            existe = cursor.execute("SELECT 1 FROM dbo.operador WHERE email = ?", email).fetchone()
            if existe:
                return None
            fila = cursor.execute(
                f"""INSERT INTO dbo.operador (email, nombre, creado_por)
                    OUTPUT {_insertadas(COLUMNAS_OPERADOR)}
                    VALUES (?, ?, ?)""",
                email,
                nombre,
                creado_por,
            ).fetchone()
        conexion.commit()
    return _operador(fila)


def activar_operador(operador_id: str, auth0_usuario_id: str, nombre: str | None) -> None:
    _ejecutar(
        """UPDATE dbo.operador
           SET estado = 'activo', auth0_usuario_id = ?, nombre = COALESCE(nombre, ?)
           WHERE id = ?""",
        auth0_usuario_id,
        nombre,
        operador_id,
    )


def registrar_ingreso_operador(operador_id: str) -> None:
    _ejecutar("UPDATE dbo.operador SET ultimo_ingreso = SYSUTCDATETIME() WHERE id = ?", operador_id)


def dar_de_baja_operador(operador_id: str) -> None:
    _ejecutar(
        """UPDATE dbo.operador SET estado = 'baja', baja_en = SYSUTCDATETIME()
           WHERE id = ? AND estado <> 'baja'""",
        operador_id,
    )


def contar_operadores_activos(excepto: str) -> int:
    """Operadores que ya ingresaron alguna vez, sin contar a ``excepto``."""
    with sqlserver.conectar(_base()) as conexion:
        return (
            conexion.cursor()
            .execute(
                "SELECT COUNT(*) FROM dbo.operador WHERE estado = 'activo' AND id <> ?", excepto
            )
            .fetchone()[0]
        )


# --- Invitaciones -------------------------------------------------------------------


def listar_invitaciones(cantidad: int = 200) -> list[Invitacion]:
    with sqlserver.conectar(_base()) as conexion:
        filas = conexion.cursor().execute(
            f"""SELECT TOP (?) {COLUMNAS_INVITACION} FROM dbo.invitacion_empresa
                ORDER BY creada_en DESC""",
            cantidad,
        )
        return [_invitacion(f) for f in filas.fetchall()]


def obtener_invitacion(invitacion_id: str) -> Invitacion | None:
    with sqlserver.conectar(_base()) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"SELECT {COLUMNAS_INVITACION} FROM dbo.invitacion_empresa WHERE id = ?",
                invitacion_id,
            )
            .fetchone()
        )
    return _invitacion(fila) if fila else None


def obtener_invitacion_abierta(email: str) -> Invitacion | None:
    """La invitación pendiente o en curso de ese email (hay a lo sumo una)."""
    with sqlserver.conectar(_base()) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS_INVITACION} FROM dbo.invitacion_empresa
                    WHERE email = ? AND estado IN ('pendiente', 'en_curso')""",
                email,
            )
            .fetchone()
        )
    return _invitacion(fila) if fila else None


def crear_invitacion(email: str, creada_por: str, vence_en: datetime) -> Invitacion:
    """Crea la invitación; una pendiente anterior del mismo email queda revocada."""
    columnas = _insertadas(COLUMNAS_INVITACION)
    with sqlserver.conectar(_base()) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """UPDATE dbo.invitacion_empresa
               SET estado = 'revocada', revocada_en = SYSUTCDATETIME()
               WHERE email = ? AND estado = 'pendiente'""",
            email,
        )
        fila = cursor.execute(
            f"""INSERT INTO dbo.invitacion_empresa (email, creada_por, vence_en)
                OUTPUT {columnas} VALUES (?, ?, ?)""",
            email,
            creada_por,
            vence_en,
        ).fetchone()
        conexion.commit()
    return _invitacion(fila)


def revocar_invitacion(invitacion_id: str) -> bool:
    return (
        _ejecutar(
            """UPDATE dbo.invitacion_empresa
               SET estado = 'revocada', revocada_en = SYSUTCDATETIME(),
                   codigo_hash = NULL, codigo_vence_en = NULL
               WHERE id = ? AND estado = 'pendiente'""",
            invitacion_id,
        )
        == 1
    )


def guardar_codigo(invitacion_id: str, codigo_hash: str, vence_en: datetime) -> None:
    """Un código nuevo reemplaza al anterior y reinicia los intentos."""
    _ejecutar(
        """UPDATE dbo.invitacion_empresa
           SET codigo_hash = ?, codigo_vence_en = ?, codigo_intentos = 0
           WHERE id = ?""",
        codigo_hash,
        vence_en,
        invitacion_id,
    )


def registrar_intento_fallido(invitacion_id: str, maximo: int) -> None:
    """Suma un intento; al llegar al máximo el código deja de servir."""
    _ejecutar(
        """UPDATE dbo.invitacion_empresa
           SET codigo_intentos = codigo_intentos + 1,
               codigo_hash = CASE WHEN codigo_intentos + 1 >= ? THEN NULL ELSE codigo_hash END
           WHERE id = ?""",
        maximo,
        invitacion_id,
    )


def consumir_codigo(invitacion_id: str) -> None:
    _ejecutar(
        """UPDATE dbo.invitacion_empresa
           SET codigo_hash = NULL, codigo_vence_en = NULL, codigo_intentos = 0
           WHERE id = ?""",
        invitacion_id,
    )


def iniciar_alta(invitacion_id: str, ahora: datetime) -> bool:
    """Pasa a en curso si sigue vigente; una ya en curso también se puede retomar."""
    return (
        _ejecutar(
            """UPDATE dbo.invitacion_empresa SET estado = 'en_curso'
               WHERE id = ?
                 AND (estado = 'en_curso' OR (estado = 'pendiente' AND vence_en > ?))""",
            invitacion_id,
            ahora,
        )
        == 1
    )


def vincular_empresa(invitacion_id: str, tenant_id: str) -> None:
    _ejecutar(
        "UPDATE dbo.invitacion_empresa SET tenant_id = ? WHERE id = ? AND tenant_id IS NULL",
        tenant_id,
        invitacion_id,
    )


def marcar_usada(invitacion_id: str) -> None:
    _ejecutar(
        """UPDATE dbo.invitacion_empresa SET estado = 'usada', usada_en = SYSUTCDATETIME()
           WHERE id = ? AND estado = 'en_curso'""",
        invitacion_id,
    )
