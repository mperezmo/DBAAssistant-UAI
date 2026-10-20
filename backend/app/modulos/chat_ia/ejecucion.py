"""Ejecución sobre la instancia real de lo que genera el chat (operaciones sincrónicas).

- Lecturas: dentro de una transacción que siempre se revierte; se devuelven las primeras
  filas y se cuentan las restantes para informar el total.
- Escrituras: antes se verifica que nadie tenga bloqueos ni transacciones abiertas sobre
  las tablas; se ejecutan en una transacción con LOCK_TIMEOUT corto para no quedar
  esperando y se revierten ante cualquier falla.
- Enmascaramiento: el origen de cada columna del resultado se obtiene del propio motor
  (sp_describe_first_result_set), lo que cubre alias y JOINs.
"""

import base64
import re
import time
from collections.abc import Iterator
from datetime import date, datetime
from datetime import time as hora
from decimal import Decimal
from uuid import UUID

import pyodbc

from app.core import cifrado
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.datos import sqlserver
from app.modulos.chat_ia.analizador import Tabla
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import Conexion

MASCARA = "****"
LOCK_TIMEOUT_MS = 5000
TAMANO_LECTURA = 5000

CONSULTA_ORIGEN_COLUMNAS = """
    EXEC sp_describe_first_result_set @tsql = ?, @params = NULL, @browse_information_mode = 1
"""
CONSULTA_BLOQUEOS = """
    SELECT DISTINCT l.request_session_id, s.login_name, s.host_name, s.program_name,
           l.request_mode, s.open_transaction_count
    FROM sys.dm_tran_locks l
    JOIN sys.dm_exec_sessions s ON s.session_id = l.request_session_id
    LEFT JOIN sys.partitions p
        ON p.hobt_id = l.resource_associated_entity_id
        AND l.resource_type IN ('PAGE', 'KEY', 'RID', 'HOBT')
    WHERE l.resource_database_id = DB_ID()
      AND l.request_session_id <> @@SPID
      AND COALESCE(p.object_id,
                   CASE WHEN l.resource_type = 'OBJECT'
                        THEN l.resource_associated_entity_id END) = OBJECT_ID(?)
"""


def _abrir(conexion: Conexion, base: str) -> pyodbc.Connection:
    try:
        abierta = sqlserver.abrir_externo(
            conexiones.servidor_de(conexion),
            conexion.usuario_sql,
            cifrado.descifrar(conexion.password_sql_cifrada),
            base,
        )
    except pyodbc.Error as error:
        raise ErrorDominio("CON_DB_000", str(error)) from error
    abierta.timeout = obtener_configuracion().chat_timeout_consulta_segundos
    return abierta


def valor_json(valor):
    """Convierte los tipos de pyodbc a valores que se pueden enviar como JSON o CSV."""
    if valor is None or isinstance(valor, bool | int | float | str):
        return valor
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, datetime | date | hora):
        return valor.isoformat()
    if isinstance(valor, bytes | bytearray):
        return "0x" + valor.hex().upper() if len(valor) <= 64 else base64.b64encode(valor).decode()
    if isinstance(valor, UUID):
        return str(valor)
    return str(valor)


def columnas_a_enmascarar(
    abierta: pyodbc.Connection, sql: str, base: str, enmascaradas: set[tuple[str, str, str]]
) -> set[int]:
    """Índices de las columnas del resultado que el usuario no puede ver."""
    if not enmascaradas:
        return set()
    try:
        filas = abierta.cursor().execute(CONSULTA_ORIGEN_COLUMNAS, sql).fetchall()
    except pyodbc.Error as error:
        # Sin poder verificar el origen de las columnas no se muestran datos sensibles.
        raise ErrorDominio(
            "USR_PER_000", f"no se pudo verificar el enmascaramiento: {error}"
        ) from error
    nombres = {columna for _, _, columna in enmascaradas}
    texto = sql.lower()
    indices, posicion = set(), 0
    for fila in filas:
        if fila.is_hidden:
            continue
        if fila.source_column:
            origen = (
                (fila.source_database or base).lower(),
                f"{fila.source_schema}.{fila.source_table}".lower(),
                fila.source_column.lower(),
            )
            if origen in enmascaradas:
                indices.add(posicion)
        elif any(re.search(rf"\b{re.escape(n)}\b", texto) for n in nombres):
            # Columna calculada: si la consulta menciona una columna sensible, se oculta.
            indices.add(posicion)
        posicion += 1
    return indices


def _fila(fila, ocultas: set[int]) -> list:
    return [
        MASCARA if i in ocultas and v is not None else valor_json(v) for i, v in enumerate(fila)
    ]


def leer(
    conexion: Conexion,
    base: str,
    sql: str,
    limite: int,
    enmascaradas: set[tuple[str, str, str]],
) -> dict:
    abierta = _abrir(conexion, base)
    abierta.autocommit = False
    inicio = time.monotonic()
    try:
        ocultas = columnas_a_enmascarar(abierta, sql, base, enmascaradas)
        cursor = abierta.cursor()
        cursor.execute(sql)
        if not cursor.description:
            return {"columnas": [], "filas": [], "total": 0, "enmascaradas": [], "duracion_ms": 0}
        columnas = [c[0] for c in cursor.description]
        filas = [_fila(f, ocultas) for f in cursor.fetchmany(limite)]
        total = len(filas)
        # El resto se cuenta sin guardarlo, para informar "N de M registros".
        while lote := cursor.fetchmany(TAMANO_LECTURA):
            total += len(lote)
        return {
            "columnas": columnas,
            "filas": filas,
            "total": total,
            "enmascaradas": sorted(ocultas),
            "duracion_ms": round((time.monotonic() - inicio) * 1000),
        }
    except pyodbc.Error as error:
        raise ErrorDominio("SQL_EXE_000", str(error)) from error
    finally:
        abierta.rollback()
        abierta.close()


def iterar(
    conexion: Conexion, base: str, sql: str, enmascaradas: set[tuple[str, str, str]]
) -> Iterator[list]:
    """Encabezado y todas las filas del resultado, para exportar sin cargarlo en memoria."""
    abierta = _abrir(conexion, base)
    abierta.autocommit = False
    try:
        ocultas = columnas_a_enmascarar(abierta, sql, base, enmascaradas)
        cursor = abierta.cursor()
        cursor.execute(sql)
        yield [c[0] for c in cursor.description or []]
        while lote := cursor.fetchmany(TAMANO_LECTURA):
            for fila in lote:
                yield _fila(fila, ocultas)
    finally:
        abierta.rollback()
        abierta.close()


def bloqueos(conexion: Conexion, base: str, tablas: tuple[Tabla, ...]) -> list[dict]:
    """Sesiones con bloqueos o transacciones abiertas sobre las tablas indicadas."""
    encontrados: dict[int, dict] = {}
    por_base: dict[str, list[Tabla]] = {}
    for tabla in tablas:
        por_base.setdefault(tabla.base, []).append(tabla)
    for nombre_base, de_la_base in por_base.items():
        abierta = _abrir(conexion, nombre_base)
        try:
            cursor = abierta.cursor()
            for tabla in de_la_base:
                for fila in cursor.execute(CONSULTA_BLOQUEOS, tabla.nombre_completo).fetchall():
                    sesion = encontrados.setdefault(
                        fila[0],
                        {
                            "sesion": fila[0],
                            "usuario": fila[1],
                            "host": fila[2],
                            "programa": fila[3],
                            "transacciones_abiertas": fila[5],
                            "tablas": [],
                        },
                    )
                    nombre = f"{nombre_base}.{tabla.nombre_completo}"
                    if nombre not in sesion["tablas"]:
                        sesion["tablas"].append(nombre)
        except pyodbc.Error as error:
            raise ErrorDominio("SQL_EXE_000", str(error)) from error
        finally:
            abierta.close()
    return list(encontrados.values())


def escribir(conexion: Conexion, base: str, sql: str) -> dict:
    abierta = _abrir(conexion, base)
    abierta.autocommit = False
    inicio = time.monotonic()
    try:
        cursor = abierta.cursor()
        cursor.execute(f"SET LOCK_TIMEOUT {LOCK_TIMEOUT_MS}")
        cursor.execute(sql)
        filas = max(cursor.rowcount, 0)
        abierta.commit()
        return {"filas_afectadas": filas, "duracion_ms": round((time.monotonic() - inicio) * 1000)}
    except pyodbc.Error as error:
        abierta.rollback()
        raise ErrorDominio("SQL_EXE_000", str(error)) from error
    finally:
        abierta.close()
