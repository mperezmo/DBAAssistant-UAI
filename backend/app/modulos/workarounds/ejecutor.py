"""Ejecución de los pasos de un workaround sobre una instancia.

Corre en un hilo aparte y guarda el progreso después de cada paso, para que la
pantalla lo muestre en tiempo real. Con un workaround transaccional, una falla revierte
todos los pasos; si no lo es, se detiene en el paso que falló e informa qué quedó
aplicado (KILL, SHRINK o REBUILD no admiten transacciones).
"""

import logging
import time
from collections.abc import Callable

import pyodbc

from app.core import cifrado
from app.datos import sqlserver, winrm
from app.modulos.conexiones.entidades import Conexion
from app.modulos.workarounds.entidades import Paso

log = logging.getLogger(__name__)

Progreso = Callable[[list[dict], str, str | None], None]


def pasos_iniciales(pasos: list[Paso]) -> list[dict]:
    return [
        {
            "descripcion": p.descripcion,
            "comando": p.sql or p.powershell,
            "estado": "pendiente",
            "mensaje": None,
            "filas": None,
            "duracion_ms": None,
        }
        for p in pasos
    ]


def _servidor(c: Conexion) -> str:
    return sqlserver.servidor_externo(c.host, c.puerto, c.instancia)


def _ejecutar_sql(cursor: pyodbc.Cursor, sql: str) -> tuple[int | None, str | None]:
    cursor.execute(sql)
    filas = cursor.rowcount if cursor.rowcount >= 0 else None
    mensajes = list(cursor.messages or [])
    # DBCC y algunos comandos devuelven varios conjuntos de resultados o mensajes.
    while cursor.nextset():
        mensajes += cursor.messages or []
    texto = " ".join(str(m[1]) for m in mensajes)[:500] or None
    return filas, texto


def _ejecutar_powershell(conexion: Conexion, script: str) -> str:
    if not conexion.tiene_winrm:
        raise RuntimeError("La conexión no tiene configurado el acceso WinRM.")
    resultado = winrm.ejecutar_powershell(
        conexion.host,
        conexion.winrm_puerto,
        conexion.winrm_https,
        conexion.winrm_usuario,
        cifrado.descifrar(conexion.winrm_password_cifrada),
        script,
    )
    if resultado.codigo != 0:
        raise RuntimeError(resultado.error.strip()[:500] or "PowerShell terminó con error.")
    return resultado.salida.strip()[:500]


def ejecutar(
    conexion: Conexion,
    base: str | None,
    pasos: list[Paso],
    transaccional: bool,
    progreso: Progreso,
) -> tuple[str, str | None]:
    """Ejecuta los pasos en orden. Devuelve el estado final y el error, si lo hubo."""
    estado_pasos = pasos_iniciales(pasos)
    abierta = None
    try:
        if any(p.sql for p in pasos):
            abierta = sqlserver.abrir_externo(
                _servidor(conexion),
                conexion.usuario_sql,
                cifrado.descifrar(conexion.password_sql_cifrada),
                base or sqlserver.BASE_SISTEMA,
            )
            abierta.autocommit = not transaccional
        cursor = abierta.cursor() if abierta else None

        for indice, paso in enumerate(pasos):
            registro = estado_pasos[indice]
            registro["estado"] = "en_curso"
            progreso(estado_pasos, "en_curso", None)
            inicio = time.monotonic()
            try:
                if paso.sql:
                    registro["filas"], registro["mensaje"] = _ejecutar_sql(cursor, paso.sql)
                else:
                    registro["mensaje"] = _ejecutar_powershell(conexion, paso.powershell)
            except Exception as error:
                registro["estado"] = "error"
                registro["mensaje"] = str(error)[:500]
                registro["duracion_ms"] = round((time.monotonic() - inicio) * 1000)
                for siguiente in estado_pasos[indice + 1 :]:
                    siguiente["estado"] = "omitido"
                if transaccional and abierta:
                    abierta.rollback()
                    for anterior in estado_pasos[:indice]:
                        anterior["estado"] = "revertido"
                    estado_final = "revertida"
                else:
                    estado_final = "fallida"
                progreso(estado_pasos, estado_final, registro["mensaje"])
                return estado_final, registro["mensaje"]
            registro["estado"] = "ok"
            registro["duracion_ms"] = round((time.monotonic() - inicio) * 1000)
            progreso(estado_pasos, "en_curso", None)

        if transaccional and abierta:
            abierta.commit()
        progreso(estado_pasos, "exitosa", None)
        return "exitosa", None
    except Exception as error:
        # Falla antes del primer paso (por ejemplo, la instancia no responde).
        mensaje = str(error)[:500]
        for registro in estado_pasos:
            if registro["estado"] in ("pendiente", "en_curso"):
                registro["estado"] = "omitido"
        progreso(estado_pasos, "fallida", mensaje)
        return "fallida", mensaje
    finally:
        if abierta:
            try:
                abierta.close()
            except pyodbc.Error:
                log.debug("La conexión del workaround ya estaba cerrada.")
