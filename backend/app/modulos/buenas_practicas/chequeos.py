"""Chequeos objetivos sobre una base (CU-000-007-A, paso 4).

Cada chequeo lee DMVs o el catálogo de la base y devuelve hallazgos con el objeto
afectado, los datos que lo justifican y, cuando se puede armar sin ambigüedad, el T-SQL
que lo corrige. La IA trabaja sobre estos hallazgos: no inventa métricas.
"""

from collections.abc import Callable

from app.web.i18n import formatear, traducir

TIPOS = ("consultas", "indices", "fragmentacion", "esquema")
MAXIMO_POR_CHEQUEO = 20
PAGINAS_MINIMAS = 1000  # índices chicos no se benefician de un rebuild
FRAGMENTACION_REORGANIZAR, FRAGMENTACION_RECONSTRUIR = 5.0, 30.0
TIEMPO_MAXIMO_SEGUNDOS = 60

CONSULTA_CONSULTAS = f"""
    SELECT TOP ({MAXIMO_POR_CHEQUEO})
           SUBSTRING(st.text, (qs.statement_start_offset / 2) + 1,
               ((CASE qs.statement_end_offset WHEN -1 THEN DATALENGTH(st.text)
                 ELSE qs.statement_end_offset END - qs.statement_start_offset) / 2) + 1),
           qs.execution_count,
           qs.total_worker_time / qs.execution_count / 1000.0,
           qs.total_elapsed_time / qs.execution_count / 1000.0,
           qs.total_logical_reads / qs.execution_count
    FROM sys.dm_exec_query_stats qs
    CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
    -- La base se toma del plan: en consultas ad hoc o autoparametrizadas st.dbid es NULL.
    CROSS APPLY (SELECT CONVERT(INT, pa.value) AS dbid
                 FROM sys.dm_exec_plan_attributes(qs.plan_handle) pa
                 WHERE pa.attribute = 'dbid') plan_base
    WHERE plan_base.dbid = DB_ID()
    ORDER BY qs.total_worker_time / qs.execution_count DESC
"""
CONSULTA_INDICES = f"""
    SELECT TOP ({MAXIMO_POR_CHEQUEO})
           OBJECT_SCHEMA_NAME(d.object_id), OBJECT_NAME(d.object_id),
           d.equality_columns, d.inequality_columns, d.included_columns,
           s.avg_user_impact, s.user_seeks + s.user_scans
    FROM sys.dm_db_missing_index_details d
    JOIN sys.dm_db_missing_index_groups g ON g.index_handle = d.index_handle
    JOIN sys.dm_db_missing_index_group_stats s ON s.group_handle = g.index_group_handle
    WHERE d.database_id = DB_ID()
    ORDER BY s.avg_user_impact * (s.user_seeks + s.user_scans) DESC
"""
CONSULTA_FRAGMENTACION = f"""
    SELECT TOP ({MAXIMO_POR_CHEQUEO})
           OBJECT_SCHEMA_NAME(ps.object_id), OBJECT_NAME(ps.object_id), i.name,
           ps.avg_fragmentation_in_percent, ps.page_count
    FROM sys.dm_db_index_physical_stats(DB_ID(), NULL, NULL, NULL, 'LIMITED') ps
    JOIN sys.indexes i ON i.object_id = ps.object_id AND i.index_id = ps.index_id
    WHERE ps.page_count > {PAGINAS_MINIMAS}
      AND ps.avg_fragmentation_in_percent > {FRAGMENTACION_REORGANIZAR}
      AND i.name IS NOT NULL AND ps.alloc_unit_type_desc = 'IN_ROW_DATA'
    ORDER BY ps.avg_fragmentation_in_percent DESC
"""
CONSULTA_SIN_PK = """
    SELECT s.name, t.name,
           CASE WHEN EXISTS (SELECT 1 FROM sys.indexes i
                             WHERE i.object_id = t.object_id AND i.type = 1) THEN 0 ELSE 1 END
    FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
    WHERE t.is_ms_shipped = 0
      AND NOT EXISTS (SELECT 1 FROM sys.indexes i
                      WHERE i.object_id = t.object_id AND i.is_primary_key = 1)
"""
CONSULTA_HEAPS = """
    SELECT s.name, t.name
    FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.indexes i ON i.object_id = t.object_id AND i.type = 0
    WHERE t.is_ms_shipped = 0
      AND EXISTS (SELECT 1 FROM sys.indexes p
                  WHERE p.object_id = t.object_id AND p.is_primary_key = 1)
"""
# Claves foráneas cuya primera columna no encabeza ningún índice: los JOIN y los DELETE
# en la tabla referenciada terminan recorriendo toda la tabla.
CONSULTA_FK_SIN_INDICE = """
    SELECT s.name, t.name, fk.name, c.name
    FROM sys.foreign_keys fk
    JOIN sys.foreign_key_columns fkc
        ON fkc.constraint_object_id = fk.object_id AND fkc.constraint_column_id = 1
    JOIN sys.tables t ON t.object_id = fk.parent_object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.columns c ON c.object_id = fkc.parent_object_id AND c.column_id = fkc.parent_column_id
    WHERE NOT EXISTS (
        SELECT 1 FROM sys.index_columns ic
        WHERE ic.object_id = fkc.parent_object_id AND ic.column_id = fkc.parent_column_id
          AND ic.key_ordinal = 1)
"""
CONSULTA_TIPOS_OBSOLETOS = """
    SELECT s.name, t.name, c.name, ty.name
    FROM sys.columns c
    JOIN sys.tables t ON t.object_id = c.object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    JOIN sys.types ty ON ty.user_type_id = c.user_type_id
    WHERE t.is_ms_shipped = 0 AND ty.name IN ('text', 'ntext', 'image')
"""
REEMPLAZOS_TIPO = {"text": "varchar(max)", "ntext": "nvarchar(max)", "image": "varbinary(max)"}


def _q(nombre: str) -> str:
    return "[" + nombre.replace("]", "]]") + "]"


def _hallazgo(tipo, severidad, objeto, descripcion, datos, sql=None) -> dict:
    return {
        "tipo": tipo,
        "severidad": severidad,
        "objeto": objeto,
        "descripcion": descripcion,
        "datos": datos,
        "sql": sql,
    }


def consultas(cursor) -> list[dict]:
    hallazgos = []
    for texto, ejecuciones, cpu_ms, duracion_ms, lecturas in cursor.execute(
        CONSULTA_CONSULTAS
    ).fetchall():
        severidad = "alta" if cpu_ms > 1000 else "media" if cpu_ms > 100 else "baja"
        hallazgos.append(
            _hallazgo(
                "consultas",
                severidad,
                " ".join((texto or "").split())[:80],
                traducir("bp.hallazgo.consulta_costosa"),
                {
                    "texto": (texto or "")[:2000],
                    "ejecuciones": ejecuciones,
                    "cpu_promedio_ms": round(float(cpu_ms), 1),
                    "duracion_promedio_ms": round(float(duracion_ms), 1),
                    "lecturas_logicas_promedio": int(lecturas),
                },
            )
        )
    return hallazgos


def indices(cursor) -> list[dict]:
    hallazgos = []
    for esquema, tabla, igualdad, desigualdad, incluidas, impacto, usos in cursor.execute(
        CONSULTA_INDICES
    ).fetchall():
        claves = ", ".join(c for c in (igualdad, desigualdad) if c)
        nombre = f"IX_{tabla}_" + "_".join(c.strip(" []") for c in claves.split(",")[:3]).replace(
            " ", ""
        )
        sql = (
            f"CREATE NONCLUSTERED INDEX {_q(nombre[:120])} ON {_q(esquema)}.{_q(tabla)} ({claves})"
        )
        if incluidas:
            sql += f" INCLUDE ({incluidas})"
        severidad = "alta" if impacto >= 70 else "media" if impacto >= 30 else "baja"
        hallazgos.append(
            _hallazgo(
                "indices",
                severidad,
                f"{esquema}.{tabla}",
                traducir("bp.hallazgo.indice_faltante"),
                {
                    "columnas_igualdad": igualdad,
                    "columnas_desigualdad": desigualdad,
                    "columnas_incluidas": incluidas,
                    "mejora_estimada_pct": round(float(impacto), 1),
                    "busquedas": int(usos),
                },
                sql + ";",
            )
        )
    return hallazgos


def fragmentacion(cursor) -> list[dict]:
    hallazgos = []
    for esquema, tabla, indice, porcentaje, paginas in cursor.execute(
        CONSULTA_FRAGMENTACION
    ).fetchall():
        reconstruir = porcentaje >= FRAGMENTACION_RECONSTRUIR
        accion = "REBUILD" if reconstruir else "REORGANIZE"
        hallazgos.append(
            _hallazgo(
                "fragmentacion",
                "alta" if reconstruir else "media",
                f"{esquema}.{tabla}.{indice}",
                formatear("bp.hallazgo.fragmentado", accion=accion),
                {"fragmentacion_pct": round(float(porcentaje), 1), "paginas": int(paginas)},
                f"ALTER INDEX {_q(indice)} ON {_q(esquema)}.{_q(tabla)} {accion};",
            )
        )
    return hallazgos


def esquema(cursor) -> list[dict]:
    hallazgos = []
    for nombre_esquema, tabla, sin_clustered in cursor.execute(CONSULTA_SIN_PK).fetchall():
        hallazgos.append(
            _hallazgo(
                "esquema",
                "alta",
                f"{nombre_esquema}.{tabla}",
                traducir(
                    "bp.hallazgo.sin_pk_ni_clustered" if sin_clustered else "bp.hallazgo.sin_pk"
                ),
                {},
            )
        )
    for nombre_esquema, tabla in cursor.execute(CONSULTA_HEAPS).fetchall():
        hallazgos.append(
            _hallazgo(
                "esquema",
                "media",
                f"{nombre_esquema}.{tabla}",
                traducir("bp.hallazgo.heap"),
                {},
            )
        )
    for nombre_esquema, tabla, fk, columna in cursor.execute(CONSULTA_FK_SIN_INDICE).fetchall():
        hallazgos.append(
            _hallazgo(
                "esquema",
                "media",
                f"{nombre_esquema}.{tabla}",
                formatear("bp.hallazgo.fk_sin_indice", fk=fk, columna=columna),
                {"clave_foranea": fk, "columna": columna},
                f"CREATE NONCLUSTERED INDEX {_q(f'IX_{tabla}_{columna}')} "
                f"ON {_q(nombre_esquema)}.{_q(tabla)} ({_q(columna)});",
            )
        )
    for nombre_esquema, tabla, columna, tipo in cursor.execute(CONSULTA_TIPOS_OBSOLETOS).fetchall():
        hallazgos.append(
            _hallazgo(
                "esquema",
                "baja",
                f"{nombre_esquema}.{tabla}.{columna}",
                formatear("bp.hallazgo.tipo_obsoleto", tipo=tipo),
                {"tipo": tipo},
                f"ALTER TABLE {_q(nombre_esquema)}.{_q(tabla)} ALTER COLUMN {_q(columna)} "
                f"{REEMPLAZOS_TIPO[tipo]};",
            )
        )
    return hallazgos[: MAXIMO_POR_CHEQUEO * 2]


CHEQUEOS: dict[str, Callable] = {
    "consultas": consultas,
    "indices": indices,
    "fragmentacion": fragmentacion,
    "esquema": esquema,
}


def ejecutar(abierta, tipos: list[str]) -> list[dict]:
    """Corre los chequeos pedidos sobre la conexión abierta en la base target."""
    abierta.timeout = TIEMPO_MAXIMO_SEGUNDOS
    cursor = abierta.cursor()
    hallazgos = []
    for tipo in tipos:
        hallazgos += CHEQUEOS[tipo](cursor)
    return hallazgos
