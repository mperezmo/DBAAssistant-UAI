"""Ejecución de T-SQL en el Sandbox: mide tiempo, filas y plan, y siempre revierte.

Todo corre dentro de una transacción que termina en ROLLBACK, así el Sandbox queda
igual a la estructura de origen. Lo que no admite transacción (CREATE DATABASE,
BACKUP, etc.) falla y se informa.
"""

import time
import xml.etree.ElementTree as ET

import pyodbc

from app.sandbox_servicio.contenedores import Destino, cadena_conexion

COLUMNA_PLAN = "Microsoft SQL Server 2005 XML Showplan"
NS = {"p": "http://schemas.microsoft.com/sqlserver/2004/07/showplan"}
FILAS_DE_MUESTRA = 100


def _resumen_plan(planes: list[str]) -> dict:
    """Operador principal, costo estimado e I/O del plan real."""
    costo = io = 0.0
    operador = None
    for xml in planes:
        try:
            raiz = ET.fromstring(xml)
        except ET.ParseError:
            continue
        for sentencia in raiz.iterfind(".//p:StmtSimple", NS):
            costo += float(sentencia.get("StatementSubTreeCost", 0) or 0)
            primero = sentencia.find(".//p:RelOp", NS)
            if operador is None and primero is not None:
                operador = primero.get("PhysicalOp")
        for relop in raiz.iterfind(".//p:RelOp", NS):
            io += float(relop.get("EstimateIO", 0) or 0)
    return {"operador": operador, "costo": round(costo, 4), "costo_io": round(io, 4)}


def _texto(valor) -> str | None:
    return None if valor is None else str(valor)[:500]


def ejecutar(
    destino: Destino, base: str, lotes: list[str], timeout: int, solo_sintaxis: bool = False
) -> dict:
    conexion = pyodbc.connect(cadena_conexion(destino, base), timeout=10, autocommit=False)
    conexion.timeout = timeout
    cursor = conexion.cursor()
    filas = 0
    planes: list[str] = []
    muestra = None
    mensajes: list[str] = []
    inicio = time.monotonic()
    try:
        # Primero se compila todo sin ejecutar: errores de sintaxis u objetos inexistentes.
        cursor.execute("SET NOEXEC ON")
        for numero, lote in enumerate(lotes, start=1):
            try:
                cursor.execute(lote)
                while cursor.nextset():
                    pass
            except pyodbc.Error as error:
                cursor.execute("SET NOEXEC OFF")
                return {"estado": "sintaxis", "error": f"Lote {numero}: {error}", "duracion_ms": 0}
        cursor.execute("SET NOEXEC OFF")
        if solo_sintaxis:
            return {"estado": "ok", "error": None, "duracion_ms": 0}

        cursor.execute("SET STATISTICS XML ON")
        inicio = time.monotonic()
        for lote in lotes:
            cursor.execute(lote)
            while True:
                if cursor.description:
                    columnas = [c[0] for c in cursor.description]
                    if columnas == [COLUMNA_PLAN]:
                        planes += [f[0] for f in cursor.fetchall()]
                    elif muestra is None:
                        datos = cursor.fetchmany(FILAS_DE_MUESTRA)
                        muestra = {
                            "columnas": columnas,
                            "filas": [[_texto(v) for v in f] for f in datos],
                        }
                    else:
                        cursor.fetchall()
                elif cursor.rowcount and cursor.rowcount > 0:
                    filas += cursor.rowcount
                mensajes += [str(m[1])[:300] for m in (cursor.messages or [])]
                if not cursor.nextset():
                    break
        estado, error = "ok", None
    except pyodbc.OperationalError as falla:
        # HYT00: se superó el tiempo máximo de ejecución.
        estado = "timeout" if "HYT00" in str(falla) else "error"
        error = str(falla)[:1000]
    except pyodbc.Error as falla:
        estado, error = "error", str(falla)[:1000]
    finally:
        duracion = round((time.monotonic() - inicio) * 1000)
        try:
            conexion.rollback()
        finally:
            conexion.close()
    return {
        "estado": estado,
        "error": error,
        "duracion_ms": duracion,
        "filas": filas,
        "muestra": muestra,
        "mensajes": mensajes[:50],
        "plan": _resumen_plan(planes),
        "plan_xml": planes[:5],
    }
