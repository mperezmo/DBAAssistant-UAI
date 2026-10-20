"""Persistencia de workarounds propios, versiones, ejecuciones, autorizaciones y
automatizaciones en la base SQL Server del tenant."""

import json
from datetime import datetime

from app.datos import sqlserver
from app.modulos.workarounds.entidades import (
    Automatizacion,
    Autorizacion,
    Ejecucion,
    Propio,
    Version,
)

# --- Workarounds propios ------------------------------------------------------------

COLUMNAS_PROPIO = """w.id, w.tenant_id, w.nombre, w.descripcion, w.categoria, w.criticidad,
    w.ambientes, w.roles, w.transaccional, w.condicion, w.version_actual, w.version_aprobada,
    w.creado_por, w.creado_en, v.pasos"""
DESDE_PROPIO = """FROM dbo.workaround AS w
    JOIN dbo.workaround_version AS v
      ON v.workaround_id = w.id AND v.version = w.version_actual"""


def _lista(texto: str) -> tuple[str, ...]:
    return tuple(x for x in (texto or "").split(",") if x)


def _propio(f) -> Propio:
    return Propio(
        id=str(f.id),
        tenant_id=str(f.tenant_id),
        nombre=f.nombre,
        descripcion=f.descripcion,
        categoria=f.categoria,
        criticidad=f.criticidad,
        ambientes=_lista(f.ambientes),
        roles=_lista(f.roles),
        transaccional=bool(f.transaccional),
        condicion=f.condicion,
        version_actual=f.version_actual,
        version_aprobada=f.version_aprobada,
        pasos=tuple(json.loads(f.pasos)),
        creado_por=f.creado_por,
        creado_en=f.creado_en,
    )


def listar_propios(base: str, tenant_id: str) -> list[Propio]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS_PROPIO} {DESDE_PROPIO}
                    WHERE w.tenant_id = ? AND w.eliminado_en IS NULL ORDER BY w.nombre""",
                tenant_id,
            )
            .fetchall()
        )
    return [_propio(f) for f in filas]


def obtener_propio(base: str, tenant_id: str, workaround_id: str) -> Propio | None:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS_PROPIO} {DESDE_PROPIO}
                    WHERE w.tenant_id = ? AND w.id = ? AND w.eliminado_en IS NULL""",
                tenant_id,
                workaround_id,
            )
            .fetchone()
        )
    return _propio(fila) if fila else None


def pasos_de_version(base: str, workaround_id: str, version: int) -> tuple[str, ...] | None:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                """SELECT pasos FROM dbo.workaround_version
                   WHERE workaround_id = ? AND version = ?""",
                workaround_id,
                version,
            )
            .fetchone()
        )
    return tuple(json.loads(fila.pasos)) if fila else None


def listar_versiones(base: str, workaround_id: str) -> list[Version]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """SELECT version, pasos, creada_por, creada_en, validada_en
                   FROM dbo.workaround_version WHERE workaround_id = ? ORDER BY version DESC""",
                workaround_id,
            )
            .fetchall()
        )
    return [
        Version(f.version, tuple(json.loads(f.pasos)), f.creada_por, f.creada_en, f.validada_en)
        for f in filas
    ]


def existe_nombre(base: str, tenant_id: str, nombre: str, excluir_id: str | None) -> bool:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                """SELECT 1 FROM dbo.workaround
                   WHERE tenant_id = ? AND nombre = ? AND eliminado_en IS NULL
                     AND (? IS NULL OR id <> ?)""",
                tenant_id,
                nombre,
                excluir_id,
                excluir_id,
            )
            .fetchone()
        )
    return fila is not None


def guardar_propio(base: str, w: Propio, usuario: str) -> str:
    """Alta, o nueva versión de uno existente. La versión aprobada no cambia: en PROD
    se sigue ejecutando la última validada en Sandbox (CU-000-005-B)."""
    datos = (
        w.nombre,
        w.descripcion,
        w.categoria,
        w.criticidad,
        ",".join(w.ambientes),
        ",".join(w.roles),
        w.transaccional,
        w.condicion,
    )
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        if not w.id:
            workaround_id = cursor.execute(
                """INSERT INTO dbo.workaround (nombre, descripcion, categoria, criticidad,
                       ambientes, roles, transaccional, condicion, version_actual, creado_por,
                       tenant_id)
                   OUTPUT inserted.id
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                *datos,
                usuario,
                w.tenant_id,
            ).fetchone()[0]
            version = 1
        else:
            workaround_id = w.id
            version = cursor.execute(
                """UPDATE dbo.workaround
                   SET nombre = ?, descripcion = ?, categoria = ?, criticidad = ?,
                       ambientes = ?, roles = ?, transaccional = ?, condicion = ?,
                       version_actual = version_actual + 1
                   OUTPUT inserted.version_actual
                   WHERE tenant_id = ? AND id = ?""",
                *datos,
                w.tenant_id,
                w.id,
            ).fetchone()[0]
        cursor.execute(
            """INSERT INTO dbo.workaround_version (workaround_id, version, pasos, creada_por)
               VALUES (?, ?, ?, ?)""",
            workaround_id,
            version,
            json.dumps(list(w.pasos)),
            usuario,
        )
        conexion.commit()
    return str(workaround_id)


def eliminar_propio(base: str, tenant_id: str, workaround_id: str) -> None:
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            """UPDATE dbo.workaround SET eliminado_en = SYSUTCDATETIME()
               WHERE tenant_id = ? AND id = ?""",
            tenant_id,
            workaround_id,
        )
        conexion.commit()


def aprobar_version(
    base: str, tenant_id: str, workaround_id: str, version: int, validacion: dict
) -> None:
    """Marca la versión como validada en Sandbox y la habilita para PROD."""
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """UPDATE dbo.workaround_version
               SET validada_en = SYSUTCDATETIME(), validacion = ?
               WHERE workaround_id = ? AND version = ?""",
            json.dumps(validacion, default=str),
            workaround_id,
            version,
        )
        cursor.execute(
            """UPDATE dbo.workaround SET version_aprobada = ?
               WHERE tenant_id = ? AND id = ?
                 AND (version_aprobada IS NULL OR version_aprobada < ?)""",
            version,
            tenant_id,
            workaround_id,
            version,
        )
        conexion.commit()


# --- Ejecuciones --------------------------------------------------------------------

COLUMNAS_EJECUCION = """id, workaround, workaround_nombre, version, conexion_id, conexion_alias,
    ambiente, base, parametros, alerta_id, origen, estado, pasos, error, ejecutada_por,
    autorizada_por, iniciada_en, finalizada_en"""


def _ejecucion(f) -> Ejecucion:
    return Ejecucion(
        id=f.id,
        workaround=f.workaround,
        workaround_nombre=f.workaround_nombre,
        version=f.version,
        conexion_id=str(f.conexion_id),
        conexion_alias=f.conexion_alias,
        ambiente=f.ambiente,
        base=f.base,
        parametros=json.loads(f.parametros),
        alerta_id=f.alerta_id,
        origen=f.origen,
        estado=f.estado,
        pasos=json.loads(f.pasos),
        error=f.error,
        ejecutada_por=f.ejecutada_por,
        autorizada_por=f.autorizada_por,
        iniciada_en=f.iniciada_en,
        finalizada_en=f.finalizada_en,
    )


def crear_ejecucion(base: str, tenant_id: str, e: Ejecucion) -> int:
    with sqlserver.conectar(base) as conexion:
        ejecucion_id = (
            conexion.cursor()
            .execute(
                """INSERT INTO dbo.ejecucion_workaround (tenant_id, workaround,
                       workaround_nombre, version, conexion_id, conexion_alias, ambiente, base,
                       parametros, alerta_id, origen, estado, pasos, ejecutada_por,
                       autorizada_por)
                   OUTPUT inserted.id
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'en_curso', ?, ?, ?)""",
                tenant_id,
                e.workaround,
                e.workaround_nombre,
                e.version,
                e.conexion_id,
                e.conexion_alias,
                e.ambiente,
                e.base,
                json.dumps(e.parametros),
                e.alerta_id,
                e.origen,
                json.dumps(e.pasos),
                e.ejecutada_por,
                e.autorizada_por,
            )
            .fetchone()[0]
        )
        conexion.commit()
    return ejecucion_id


def actualizar_ejecucion(
    base: str, ejecucion_id: int, pasos: list[dict], estado: str, error: str | None
) -> None:
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            """UPDATE dbo.ejecucion_workaround
               SET pasos = ?, estado = ?, error = ?,
                   finalizada_en = CASE WHEN ? = 'en_curso' THEN NULL ELSE SYSUTCDATETIME() END
               WHERE id = ?""",
            json.dumps(pasos),
            estado,
            (error or "")[:1000] or None,
            estado,
            ejecucion_id,
        )
        conexion.commit()


def obtener_ejecucion(base: str, tenant_id: str, ejecucion_id: int) -> Ejecucion | None:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS_EJECUCION} FROM dbo.ejecucion_workaround
                    WHERE tenant_id = ? AND id = ?""",
                tenant_id,
                ejecucion_id,
            )
            .fetchone()
        )
    return _ejecucion(fila) if fila else None


def listar_ejecuciones(
    base: str, tenant_id: str, workaround: str | None = None, limite: int = 50
) -> list[Ejecucion]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""SELECT TOP (?) {COLUMNAS_EJECUCION} FROM dbo.ejecucion_workaround
                    WHERE tenant_id = ? AND (? IS NULL OR workaround = ?)
                    ORDER BY iniciada_en DESC, id DESC""",
                limite,
                tenant_id,
                workaround,
                workaround,
            )
            .fetchall()
        )
    return [_ejecucion(f) for f in filas]


def estadisticas(base: str, tenant_id: str) -> dict[str, tuple[int, datetime]]:
    """Cantidad de ejecuciones y la última, por workaround."""
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """SELECT workaround, COUNT(*), MAX(iniciada_en) FROM dbo.ejecucion_workaround
                   WHERE tenant_id = ? GROUP BY workaround""",
                tenant_id,
            )
            .fetchall()
        )
    return {f[0]: (f[1], f[2]) for f in filas}


def cerrar_interrumpidas(base: str, tenant_id: str) -> int:
    """Ejecuciones que quedaron en curso porque el backend se detuvo."""
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """UPDATE dbo.ejecucion_workaround
                   SET estado = 'fallida', error = N'Interrumpida por un reinicio del sistema.',
                       finalizada_en = SYSUTCDATETIME()
                   WHERE tenant_id = ? AND estado = 'en_curso'""",
                tenant_id,
            )
            .rowcount
        )
        conexion.commit()
    return filas


# --- Autorizaciones fuera de la ventana de mantenimiento ----------------------------

COLUMNAS_AUTORIZACION = """id, workaround, workaround_nombre, conexion_id, conexion_alias,
    motivo, solicitada_por, solicitada_en, estado, resuelta_por, resuelta_en, vence_en"""


def _autorizacion(f) -> Autorizacion:
    return Autorizacion(
        id=str(f.id),
        workaround=f.workaround,
        workaround_nombre=f.workaround_nombre,
        conexion_id=str(f.conexion_id),
        conexion_alias=f.conexion_alias,
        motivo=f.motivo,
        solicitada_por=f.solicitada_por,
        solicitada_en=f.solicitada_en,
        estado=f.estado,
        resuelta_por=f.resuelta_por,
        resuelta_en=f.resuelta_en,
        vence_en=f.vence_en,
    )


def crear_autorizacion(
    base: str,
    tenant_id: str,
    workaround: tuple[str, str],
    conexion: tuple[str, str],
    motivo: str,
    usuario: str,
    horas: int,
) -> str:
    with sqlserver.conectar(base) as conexion_sql:
        autorizacion_id = (
            conexion_sql.cursor()
            .execute(
                """INSERT INTO dbo.autorizacion_workaround (tenant_id, workaround,
                       workaround_nombre, conexion_id, conexion_alias, motivo, solicitada_por,
                       vence_en)
                   OUTPUT inserted.id
                   VALUES (?, ?, ?, ?, ?, ?, ?, DATEADD(HOUR, ?, SYSUTCDATETIME()))""",
                tenant_id,
                *workaround,
                *conexion,
                motivo,
                usuario,
                horas,
            )
            .fetchone()[0]
        )
        conexion_sql.commit()
    return str(autorizacion_id)


def listar_autorizaciones(base: str, tenant_id: str) -> list[Autorizacion]:
    """Pendientes y autorizadas sin usar, todavía vigentes."""
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS_AUTORIZACION} FROM dbo.autorizacion_workaround
                    WHERE tenant_id = ? AND estado IN ('pendiente', 'autorizada')
                      AND vence_en > SYSUTCDATETIME()
                    ORDER BY solicitada_en DESC""",
                tenant_id,
            )
            .fetchall()
        )
    return [_autorizacion(f) for f in filas]


def obtener_autorizacion(base: str, tenant_id: str, autorizacion_id: str) -> Autorizacion | None:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS_AUTORIZACION} FROM dbo.autorizacion_workaround
                    WHERE tenant_id = ? AND id = ?""",
                tenant_id,
                autorizacion_id,
            )
            .fetchone()
        )
    return _autorizacion(fila) if fila else None


def resolver_autorizacion(
    base: str, tenant_id: str, autorizacion_id: str, usuario: str, aprobar: bool, horas: int
) -> bool:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """UPDATE dbo.autorizacion_workaround
                   SET estado = ?, resuelta_por = ?, resuelta_en = SYSUTCDATETIME(),
                       vence_en = CASE WHEN ? = 1 THEN DATEADD(HOUR, ?, SYSUTCDATETIME())
                                       ELSE vence_en END
                   WHERE tenant_id = ? AND id = ? AND estado = 'pendiente'
                     AND vence_en > SYSUTCDATETIME()""",
                "autorizada" if aprobar else "rechazada",
                usuario,
                aprobar,
                horas,
                tenant_id,
                autorizacion_id,
            )
            .rowcount
        )
        conexion.commit()
    return filas == 1


def consumir_autorizacion(
    base: str, tenant_id: str, workaround: str, conexion_id: str, usuario: str
) -> str | None:
    """Usa una autorización vigente del solicitante. Devuelve quién la autorizó."""
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                """UPDATE TOP (1) dbo.autorizacion_workaround SET estado = 'usada'
                   OUTPUT inserted.resuelta_por
                   WHERE tenant_id = ? AND workaround = ? AND conexion_id = ?
                     AND solicitada_por = ? AND estado = 'autorizada'
                     AND vence_en > SYSUTCDATETIME()""",
                tenant_id,
                workaround,
                conexion_id,
                usuario,
            )
            .fetchone()
        )
        conexion.commit()
    return fila[0] if fila else None


# --- Automatizaciones ---------------------------------------------------------------


def listar_automatizaciones(base: str, tenant_id: str) -> list[Automatizacion]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """SELECT id, evento, workaround, conexion_id, activa, creada_por
                   FROM dbo.automatizacion_workaround WHERE tenant_id = ?
                   ORDER BY creada_en""",
                tenant_id,
            )
            .fetchall()
        )
    return [
        Automatizacion(
            str(f.id),
            f.evento,
            f.workaround,
            str(f.conexion_id) if f.conexion_id else None,
            bool(f.activa),
            f.creada_por,
        )
        for f in filas
    ]


def crear_automatizacion(base: str, tenant_id: str, a: Automatizacion) -> str:
    with sqlserver.conectar(base) as conexion:
        automatizacion_id = (
            conexion.cursor()
            .execute(
                """INSERT INTO dbo.automatizacion_workaround (tenant_id, evento, workaround,
                       conexion_id, activa, creada_por)
                   OUTPUT inserted.id VALUES (?, ?, ?, ?, ?, ?)""",
                tenant_id,
                a.evento,
                a.workaround,
                a.conexion_id,
                a.activa,
                a.creada_por,
            )
            .fetchone()[0]
        )
        conexion.commit()
    return str(automatizacion_id)


def eliminar_automatizacion(base: str, tenant_id: str, automatizacion_id: str) -> bool:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                "DELETE FROM dbo.automatizacion_workaround WHERE tenant_id = ? AND id = ?",
                tenant_id,
                automatizacion_id,
            )
            .rowcount
        )
        conexion.commit()
    return filas == 1
