"""Snapshot de la estructura de una base y diferencias entre dos snapshots.

Se lee del catálogo de la base (sin sqlpackage): tablas con sus columnas, claves e
índices (y el versionado si son temporales), y el código de vistas, procedimientos,
funciones y triggers, incluidos los de base de datos. Cada objeto queda como un texto DDL
legible, así el diff se puede mostrar lado a lado.
"""

from collections import defaultdict

import pyodbc

CONSULTA_COLUMNAS = """
    SELECT s.name, t.name, c.name, ty.name, c.max_length, c.precision, c.scale,
           c.is_nullable, c.is_identity, dc.definition, c.column_id
    FROM sys.tables t
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.columns c ON c.object_id = t.object_id
    JOIN sys.types ty ON ty.user_type_id = c.user_type_id
    LEFT JOIN sys.default_constraints dc ON dc.object_id = c.default_object_id
    WHERE t.is_ms_shipped = 0
    ORDER BY s.name, t.name, c.column_id
"""
CONSULTA_INDICES = """
    SELECT s.name, t.name, i.name, i.is_primary_key, i.is_unique, i.type_desc,
           c.name, ic.is_descending_key, ic.is_included_column, ic.key_ordinal,
           ic.index_column_id, i.filter_definition
    FROM sys.indexes i
    JOIN sys.tables t ON t.object_id = i.object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
    JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
    WHERE t.is_ms_shipped = 0 AND i.name IS NOT NULL
    ORDER BY s.name, t.name, i.name, ic.key_ordinal, ic.index_column_id
"""
CONSULTA_FK = """
    SELECT s.name, t.name, fk.name, cp.name, sr.name, tr.name, cr.name,
           fk.delete_referential_action_desc, fkc.constraint_column_id
    FROM sys.foreign_keys fk
    JOIN sys.tables t ON t.object_id = fk.parent_object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
    JOIN sys.columns cp ON cp.object_id = fkc.parent_object_id
        AND cp.column_id = fkc.parent_column_id
    JOIN sys.tables tr ON tr.object_id = fk.referenced_object_id
    JOIN sys.schemas sr ON sr.schema_id = tr.schema_id
    JOIN sys.columns cr ON cr.object_id = fkc.referenced_object_id
        AND cr.column_id = fkc.referenced_column_id
    ORDER BY s.name, t.name, fk.name, fkc.constraint_column_id
"""
CONSULTA_MODULOS = """
    SELECT o.type, s.name, o.name, m.definition
    FROM sys.sql_modules m
    JOIN sys.objects o ON o.object_id = m.object_id
    JOIN sys.schemas s ON s.schema_id = o.schema_id
    WHERE o.is_ms_shipped = 0 AND o.type IN ('V', 'P', 'FN', 'IF', 'TF', 'TR')
"""
# Tablas temporales (SQL Server 2017+): período y tabla de historia. En versiones
# anteriores la consulta falla y las tablas se describen sin versionado.
CONSULTA_TEMPORALES = """
    SELECT s.name, t.name, cs.name, cs.is_hidden, ce.name, ce.is_hidden, sh.name, th.name,
           t.history_retention_period, t.history_retention_period_unit_desc
    FROM sys.tables t
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.periods p ON p.object_id = t.object_id
    JOIN sys.columns cs ON cs.object_id = t.object_id AND cs.column_id = p.start_column_id
    JOIN sys.columns ce ON ce.object_id = t.object_id AND ce.column_id = p.end_column_id
    JOIN sys.tables th ON th.object_id = t.history_table_id
    JOIN sys.schemas sh ON sh.schema_id = th.schema_id
    WHERE t.temporal_type = 2
"""
# Triggers de base de datos (DDL): no figuran en sys.objects.
CONSULTA_TRIGGERS_BASE = """
    SELECT tr.name, m.definition
    FROM sys.triggers tr
    JOIN sys.sql_modules m ON m.object_id = tr.object_id
    WHERE tr.parent_class = 0 AND tr.is_ms_shipped = 0
"""
TIPOS_MODULO = {
    "V": "vista",
    "P": "procedimiento",
    "FN": "funcion",
    "IF": "funcion",
    "TF": "funcion",
    "TR": "trigger",
}


def _q(nombre: str) -> str:
    return "[" + nombre.replace("]", "]]") + "]"


def _tipo(nombre: str, largo: int, precision: int, escala: int) -> str:
    if nombre in ("varchar", "char", "varbinary", "binary"):
        return f"{nombre}({'max' if largo == -1 else largo})"
    if nombre in ("nvarchar", "nchar"):
        return f"{nombre}({'max' if largo == -1 else largo // 2})"
    if nombre in ("decimal", "numeric"):
        return f"{nombre}({precision}, {escala})"
    if nombre in ("datetime2", "time", "datetimeoffset"):
        return f"{nombre}({escala})"
    return nombre


def _temporales(cursor) -> dict[tuple[str, str], dict]:
    try:
        filas = cursor.execute(CONSULTA_TEMPORALES).fetchall()
    except pyodbc.Error:
        return {}
    temporales = {}
    for fila in filas:
        esquema, tabla, inicio, inicio_oculto, fin, fin_oculto, *historia, periodo, unidad = fila
        infinita = periodo is None or periodo < 0 or unidad == "INFINITE"
        retencion = "INFINITE" if infinita else f"{periodo} {unidad}S"
        tabla_historia = f"{_q(historia[0])}.{_q(historia[1])}"
        temporales[(esquema, tabla)] = {
            "columnas": {inicio: ("START", inicio_oculto), fin: ("END", fin_oculto)},
            "periodo": f"    PERIOD FOR SYSTEM_TIME ({_q(inicio)}, {_q(fin)})",
            "versionado": (
                f" WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = {tabla_historia},"
                f" HISTORY_RETENTION_PERIOD = {retencion}))"
            ),
        }
    return temporales


def capturar(abierta) -> dict[str, dict]:
    """Objetos de la base abierta, por clave ``tipo:esquema.nombre``."""
    cursor = abierta.cursor()
    temporales = _temporales(cursor)
    columnas: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (
        esquema,
        tabla,
        col,
        tipo,
        largo,
        precision,
        escala,
        nula,
        identidad,
        defecto,
        _,
    ) in cursor.execute(CONSULTA_COLUMNAS).fetchall():
        linea = f"    {_q(col)} {_tipo(tipo, largo, precision, escala)}"
        linea += " IDENTITY" if identidad else ""
        periodo = temporales.get((esquema, tabla), {}).get("columnas", {}).get(col)
        if periodo:
            linea += f" GENERATED ALWAYS AS ROW {periodo[0]}" + (" HIDDEN" if periodo[1] else "")
        linea += " NULL" if nula else " NOT NULL"
        linea += f" DEFAULT {defecto}" if defecto else ""
        columnas[(esquema, tabla)].append(linea)

    restricciones: dict[tuple[str, str], list[str]] = defaultdict(list)
    objetos: dict[str, dict] = {}
    indices: dict[tuple, dict] = {}
    for fila in cursor.execute(CONSULTA_INDICES).fetchall():
        esquema, tabla, nombre, es_pk, unico, tipo, col, desc, incluida, *_, filtro = fila
        clave = (esquema, tabla, nombre)
        datos = indices.setdefault(
            clave,
            {
                "pk": es_pk,
                "unico": unico,
                "tipo": tipo,
                "claves": [],
                "incluidas": [],
                "filtro": filtro,
            },
        )
        if incluida:
            datos["incluidas"].append(_q(col))
        else:
            datos["claves"].append(_q(col) + (" DESC" if desc else ""))
    for (esquema, tabla, nombre), d in indices.items():
        lista = ", ".join(d["claves"])
        if d["pk"]:
            restricciones[(esquema, tabla)].append(
                f"    CONSTRAINT {_q(nombre)} PRIMARY KEY {d['tipo']} ({lista})"
            )
            continue
        ddl = (
            f"CREATE {'UNIQUE ' if d['unico'] else ''}{d['tipo']} INDEX {_q(nombre)}"
            f" ON {_q(esquema)}.{_q(tabla)} ({lista})"
        )
        if d["incluidas"]:
            ddl += f" INCLUDE ({', '.join(d['incluidas'])})"
        if d["filtro"]:
            ddl += f" WHERE {d['filtro']}"
        objetos[f"indice:{esquema}.{tabla}.{nombre}"] = {
            "tipo": "indice",
            "nombre": f"{esquema}.{tabla}.{nombre}",
            "definicion": ddl + ";",
        }

    fks: dict[tuple, dict] = {}
    for esquema, tabla, nombre, cp, er, tr, cr, accion, _ in cursor.execute(CONSULTA_FK).fetchall():
        d = fks.setdefault(
            (esquema, tabla, nombre),
            {"de": [], "a": [], "ref": f"{_q(er)}.{_q(tr)}", "accion": accion},
        )
        d["de"].append(_q(cp))
        d["a"].append(_q(cr))
    for (esquema, tabla, nombre), d in fks.items():
        linea = (
            f"    CONSTRAINT {_q(nombre)} FOREIGN KEY ({', '.join(d['de'])})"
            f" REFERENCES {d['ref']} ({', '.join(d['a'])})"
        )
        if d["accion"] != "NO_ACTION":
            linea += f" ON DELETE {d['accion'].replace('_', ' ')}"
        restricciones[(esquema, tabla)].append(linea)

    for (esquema, tabla), lineas in columnas.items():
        temporal = temporales.get((esquema, tabla))
        periodo = [temporal["periodo"]] if temporal else []
        cuerpo = ",\n".join(lineas + periodo + restricciones.get((esquema, tabla), []))
        versionado = temporal["versionado"] if temporal else ""
        objetos[f"tabla:{esquema}.{tabla}"] = {
            "tipo": "tabla",
            "nombre": f"{esquema}.{tabla}",
            "definicion": f"CREATE TABLE {_q(esquema)}.{_q(tabla)} (\n{cuerpo}\n){versionado};",
        }

    for tipo, esquema, nombre, definicion in cursor.execute(CONSULTA_MODULOS).fetchall():
        clase = TIPOS_MODULO[tipo.strip()]
        objetos[f"{clase}:{esquema}.{nombre}"] = {
            "tipo": clase,
            "nombre": f"{esquema}.{nombre}",
            "definicion": _sin_espacios_finales(definicion),
        }
    try:
        triggers_base = cursor.execute(CONSULTA_TRIGGERS_BASE).fetchall()
    except pyodbc.Error:
        triggers_base = []
    for nombre, definicion in triggers_base:
        objetos[f"trigger:{nombre}"] = {
            "tipo": "trigger",
            "nombre": nombre,
            "definicion": _sin_espacios_finales(definicion),
        }
    return objetos


def _sin_espacios_finales(definicion: str | None) -> str:
    # Sin espacios finales por renglón: el motor a veces los agrega al guardar.
    return "\n".join(r.rstrip() for r in (definicion or "").strip().splitlines())


def diferencias(antes: dict[str, dict], despues: dict[str, dict]) -> list[dict]:
    """Objetos agregados, eliminados y modificados, ordenados por tipo y nombre."""
    cambios = []
    for clave in sorted(antes.keys() | despues.keys()):
        anterior, nuevo = antes.get(clave), despues.get(clave)
        if anterior and nuevo and anterior["definicion"] == nuevo["definicion"]:
            continue
        objeto = nuevo or anterior
        cambios.append(
            {
                "operacion": "agregado"
                if not anterior
                else "eliminado"
                if not nuevo
                else "modificado",
                "tipo": objeto["tipo"],
                "nombre": objeto["nombre"],
                "antes": anterior["definicion"] if anterior else None,
                "despues": nuevo["definicion"] if nuevo else None,
            }
        )
    return cambios
