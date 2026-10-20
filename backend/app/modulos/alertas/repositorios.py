"""Persistencia de reglas y alertas en la base SQL Server del tenant."""

from app.datos import sqlserver
from app.modulos.alertas.entidades import (
    Alerta,
    Condicion,
    Desestimada,
    Regla,
    ResultadoDisparo,
    rango_severidad,
)

COLUMNAS_REGLA = (
    "id, tenant_id, nombre, metrica, operador, valor, severidad, conexion_id,"
    " sostenida_segundos, destinatarios, push, activa, creada_por, email"
)
COLUMNAS_ALERTA = (
    "id, tenant_id, conexion_id, conexion_alias, servidor, ambiente, origen, clave, regla_id,"
    " titulo, descripcion, severidad, metrica, valor, umbral, unidad, estado, normalizada,"
    " ocurrencias, disparada_en, actualizada_en, normalizada_en, atendida_en, atendida_por,"
    " accion, comentario"
)
ORDEN_SEVERIDAD = "CASE severidad WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END"


def _regla(f) -> Regla:
    return Regla(
        id=str(f.id),
        tenant_id=str(f.tenant_id),
        nombre=f.nombre,
        metrica=f.metrica,
        operador=f.operador,
        valor=f.valor,
        severidad=f.severidad,
        conexion_id=str(f.conexion_id) if f.conexion_id else None,
        sostenida_segundos=f.sostenida_segundos,
        destinatarios=tuple(r for r in f.destinatarios.split(",") if r),
        push=bool(f.push),
        activa=bool(f.activa),
        creada_por=f.creada_por,
        email=bool(f.email),
    )


def _alerta(f) -> Alerta:
    return Alerta(
        id=f.id,
        tenant_id=str(f.tenant_id),
        conexion_id=str(f.conexion_id),
        conexion_alias=f.conexion_alias,
        servidor=f.servidor,
        ambiente=f.ambiente,
        origen=f.origen,
        clave=f.clave,
        regla_id=str(f.regla_id) if f.regla_id else None,
        titulo=f.titulo,
        descripcion=f.descripcion,
        severidad=f.severidad,
        metrica=f.metrica,
        valor=f.valor,
        umbral=f.umbral,
        unidad=f.unidad,
        estado=f.estado,
        normalizada=bool(f.normalizada),
        ocurrencias=f.ocurrencias,
        disparada_en=f.disparada_en,
        actualizada_en=f.actualizada_en,
        normalizada_en=f.normalizada_en,
        atendida_en=f.atendida_en,
        atendida_por=f.atendida_por,
        accion=f.accion,
        comentario=f.comentario,
    )


# --- Reglas -------------------------------------------------------------------------


def listar_reglas(base: str, tenant_id: str) -> list[Regla]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS_REGLA} FROM dbo.regla_alerta
                    WHERE tenant_id = ? AND eliminada_en IS NULL ORDER BY nombre""",
                tenant_id,
            )
            .fetchall()
        )
    return [_regla(f) for f in filas]


def obtener_regla(base: str, tenant_id: str, regla_id: str) -> Regla | None:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS_REGLA} FROM dbo.regla_alerta
                    WHERE tenant_id = ? AND id = ? AND eliminada_en IS NULL""",
                tenant_id,
                regla_id,
            )
            .fetchone()
        )
    return _regla(fila) if fila else None


def existe_nombre(base: str, tenant_id: str, nombre: str, excluir_id: str | None) -> bool:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                """SELECT 1 FROM dbo.regla_alerta
                   WHERE tenant_id = ? AND nombre = ? AND eliminada_en IS NULL
                     AND (? IS NULL OR id <> ?)""",
                tenant_id,
                nombre,
                excluir_id,
                excluir_id,
            )
            .fetchone()
        )
    return fila is not None


def guardar_regla(base: str, r: Regla, nueva: bool) -> str:
    valores = (
        r.nombre,
        r.metrica,
        r.operador,
        r.valor,
        r.severidad,
        r.conexion_id,
        r.sostenida_segundos,
        ",".join(r.destinatarios),
        r.push,
        r.activa,
        r.email,
    )
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        if nueva:
            regla_id = cursor.execute(
                """INSERT INTO dbo.regla_alerta (nombre, metrica, operador, valor, severidad,
                       conexion_id, sostenida_segundos, destinatarios, push, activa, email,
                       creada_por, tenant_id)
                   OUTPUT inserted.id
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                *valores,
                r.creada_por,
                r.tenant_id,
            ).fetchone()[0]
        else:
            regla_id = r.id
            cursor.execute(
                """UPDATE dbo.regla_alerta
                   SET nombre = ?, metrica = ?, operador = ?, valor = ?, severidad = ?,
                       conexion_id = ?, sostenida_segundos = ?, destinatarios = ?, push = ?,
                       activa = ?, email = ?
                   WHERE tenant_id = ? AND id = ?""",
                *valores,
                r.tenant_id,
                r.id,
            )
        conexion.commit()
    return str(regla_id)


def eliminar_regla(base: str, tenant_id: str, regla_id: str, usuario: str) -> int:
    """Baja lógica; cierra las alertas activas de la regla. Devuelve cuántas cerró."""
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """UPDATE dbo.regla_alerta SET eliminada_en = SYSUTCDATETIME(), activa = 0
               WHERE tenant_id = ? AND id = ?""",
            tenant_id,
            regla_id,
        )
        cerradas = cursor.execute(
            """UPDATE dbo.alerta
               SET estado = 'atendida', accion = 'regla_eliminada', atendida_por = ?,
                   atendida_en = SYSUTCDATETIME(), actualizada_en = SYSUTCDATETIME()
               WHERE tenant_id = ? AND regla_id = ? AND estado = 'activa'""",
            usuario,
            tenant_id,
            regla_id,
        ).rowcount
        conexion.commit()
    return cerradas


# --- Alertas ------------------------------------------------------------------------


def disparar(
    base: str,
    tenant_id: str,
    conexion_id: str,
    datos_conexion: tuple[str, str, str],
    c: Condicion,
    solo_existente: bool = False,
) -> ResultadoDisparo | None:
    """Crea la alerta o actualiza la activa de la misma condición (una por conexión y clave).

    Si la alerta estaba normalizada y la condición vuelve, cuenta una ocurrencia más.
    La severidad nunca baja mientras la alerta siga activa. Con ``solo_existente`` no
    crea una alerta nueva y devuelve None si no hay una activa.
    """
    alias, servidor, ambiente = datos_conexion
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        actual = cursor.execute(
            f"""SELECT {COLUMNAS_ALERTA} FROM dbo.alerta WITH (UPDLOCK, HOLDLOCK)
                WHERE tenant_id = ? AND conexion_id = ? AND clave = ? AND estado = 'activa'""",
            tenant_id,
            conexion_id,
            c.clave,
        ).fetchone()
        if actual is None and solo_existente:
            conexion.rollback()
            return None
        if actual is None:
            alerta_id = cursor.execute(
                """INSERT INTO dbo.alerta (tenant_id, conexion_id, conexion_alias, servidor,
                       ambiente, origen, clave, regla_id, titulo, descripcion, severidad,
                       metrica, valor, umbral, unidad)
                   OUTPUT inserted.id
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tenant_id,
                conexion_id,
                alias,
                servidor,
                ambiente,
                c.origen,
                c.clave,
                c.regla_id,
                c.titulo,
                c.descripcion,
                c.severidad,
                c.metrica,
                c.valor,
                c.umbral,
                c.unidad,
            ).fetchone()[0]
            nueva, escalo = True, False
        else:
            alerta_id = actual.id
            nueva = False
            escalo = rango_severidad(c.severidad) > rango_severidad(actual.severidad)
            severidad = c.severidad if escalo else actual.severidad
            # Al escalar se toman título, descripción y umbral del nivel nuevo.
            titulo = c.titulo if escalo else actual.titulo
            umbral = c.umbral if escalo else actual.umbral
            cursor.execute(
                """UPDATE dbo.alerta
                   SET severidad = ?, titulo = ?, umbral = ?, valor = ?, descripcion = ?,
                       ocurrencias = ocurrencias + CASE WHEN normalizada = 1 THEN 1 ELSE 0 END,
                       normalizada = 0, normalizada_en = NULL,
                       actualizada_en = SYSUTCDATETIME()
                   WHERE id = ?""",
                severidad,
                titulo,
                umbral,
                c.valor,
                c.descripcion,
                alerta_id,
            )
        conexion.commit()
        fila = cursor.execute(
            f"SELECT {COLUMNAS_ALERTA} FROM dbo.alerta WHERE id = ?", alerta_id
        ).fetchone()
    return ResultadoDisparo(_alerta(fila), nueva, escalo)


def normalizar(base: str, tenant_id: str, conexion_id: str, clave: str) -> None:
    """La condición dejó de cumplirse: la alerta sigue activa hasta que la atiendan.

    También se marcan las ya atendidas: así, tras un reinicio, el motor distingue una
    condición que persiste desde que la atendieron (no se vuelve a alertar) de una nueva.
    """
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            """UPDATE dbo.alerta
               SET normalizada = 1, normalizada_en = SYSUTCDATETIME(),
                   actualizada_en = SYSUTCDATETIME()
               WHERE tenant_id = ? AND conexion_id = ? AND clave = ? AND normalizada = 0""",
            tenant_id,
            conexion_id,
            clave,
        )
        conexion.commit()


def claves_activas(base: str, tenant_id: str) -> list[tuple[str, str, str]]:
    """(conexion_id, clave, severidad) de las condiciones en curso.

    Incluye las alertas atendidas cuya condición no se normalizó todavía, para no
    volver a crearlas al reiniciar el backend.
    """
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """SELECT DISTINCT conexion_id, clave, severidad FROM dbo.alerta
                   WHERE tenant_id = ? AND normalizada = 0""",
                tenant_id,
            )
            .fetchall()
        )
    return [(str(f.conexion_id), f.clave, f.severidad) for f in filas]


def listar(base: str, tenant_id: str, estado: str, limite: int = 200) -> list[Alerta]:
    orden = f"{ORDEN_SEVERIDAD}, disparada_en DESC" if estado == "activa" else "atendida_en DESC"
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""SELECT TOP (?) {COLUMNAS_ALERTA} FROM dbo.alerta
                    WHERE tenant_id = ? AND estado = ? ORDER BY {orden}""",
                limite,
                tenant_id,
                estado,
            )
            .fetchall()
        )
    return [_alerta(f) for f in filas]


def obtener(base: str, tenant_id: str, alerta_id: int) -> Alerta | None:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"SELECT {COLUMNAS_ALERTA} FROM dbo.alerta WHERE tenant_id = ? AND id = ?",
                tenant_id,
                alerta_id,
            )
            .fetchone()
        )
    return _alerta(fila) if fila else None


def atender(
    base: str, tenant_id: str, alerta_id: int, usuario: str, accion: str, comentario: str
) -> bool:
    """Marca la alerta como atendida. Devuelve False si ya no estaba activa."""
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """UPDATE dbo.alerta
                   SET estado = 'atendida', atendida_por = ?, atendida_en = SYSUTCDATETIME(),
                       accion = ?, comentario = ?, actualizada_en = SYSUTCDATETIME()
                   WHERE tenant_id = ? AND id = ? AND estado = 'activa'""",
                usuario,
                accion,
                comentario,
                tenant_id,
                alerta_id,
            )
            .rowcount
        )
        conexion.commit()
    return filas == 1


def contar_activas(base: str, tenant_id: str) -> dict[str, int]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """SELECT severidad, COUNT(*) FROM dbo.alerta
                   WHERE tenant_id = ? AND estado = 'activa' GROUP BY severidad""",
                tenant_id,
            )
            .fetchall()
        )
    return {severidad: cantidad for severidad, cantidad in filas}


# --- Desestimadas -------------------------------------------------------------------

VIGENTE = "reactivada_en IS NULL AND (hasta IS NULL OR hasta > SYSUTCDATETIME())"


def _desestimada(f) -> Desestimada:
    return Desestimada(
        id=str(f.id),
        conexion_id=str(f.conexion_id),
        clave=f.clave,
        titulo=f.titulo,
        conexion_alias=f.conexion_alias,
        motivo=f.motivo,
        hasta=f.hasta,
        creada_por=f.creada_por,
        creada_en=f.creada_en,
    )


def desestimar(base: str, tenant_id: str, alerta: Alerta, usuario: str, motivo: str, hasta) -> bool:
    """Registra la desestimación y cierra la alerta en una transacción.

    Devuelve False si la alerta ya no estaba activa.
    """
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        cerrada = cursor.execute(
            """UPDATE dbo.alerta
               SET estado = 'atendida', accion = 'desestimada', comentario = ?,
                   atendida_por = ?, atendida_en = SYSUTCDATETIME(),
                   actualizada_en = SYSUTCDATETIME()
               WHERE tenant_id = ? AND id = ? AND estado = 'activa'""",
            motivo,
            usuario,
            tenant_id,
            alerta.id,
        ).rowcount
        if cerrada != 1:
            conexion.rollback()
            return False
        # Una desestimación anterior de la misma condición (vencida) se da por terminada.
        cursor.execute(
            """UPDATE dbo.alerta_desestimada
               SET reactivada_en = SYSUTCDATETIME(), reactivada_por = ?
               WHERE tenant_id = ? AND conexion_id = ? AND clave = ? AND reactivada_en IS NULL""",
            usuario,
            tenant_id,
            alerta.conexion_id,
            alerta.clave,
        )
        cursor.execute(
            """INSERT INTO dbo.alerta_desestimada (tenant_id, conexion_id, clave, titulo,
                   conexion_alias, motivo, hasta, creada_por)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            tenant_id,
            alerta.conexion_id,
            alerta.clave,
            alerta.titulo,
            alerta.conexion_alias,
            motivo,
            hasta,
            usuario,
        )
        conexion.commit()
    return True


def listar_desestimadas(base: str, tenant_id: str) -> list[Desestimada]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""SELECT id, conexion_id, clave, titulo, conexion_alias, motivo, hasta,
                           creada_por, creada_en
                    FROM dbo.alerta_desestimada
                    WHERE tenant_id = ? AND {VIGENTE} ORDER BY creada_en DESC""",
                tenant_id,
            )
            .fetchall()
        )
    return [_desestimada(f) for f in filas]


def reactivar(base: str, tenant_id: str, desestimada_id: str, usuario: str) -> bool:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""UPDATE dbo.alerta_desestimada
                    SET reactivada_en = SYSUTCDATETIME(), reactivada_por = ?
                    WHERE tenant_id = ? AND id = ? AND {VIGENTE}""",
                usuario,
                tenant_id,
                desestimada_id,
            )
            .rowcount
        )
        conexion.commit()
    return filas == 1
