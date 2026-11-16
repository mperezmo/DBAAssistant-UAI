"""Consultas costosas y su tendencia desde el Query Store de cada base (SQL Server 2016+).

Query Store guarda en la propia base las estadísticas de ejecución por intervalos (por
defecto, de una hora) y los planes de cada consulta, con su propia retención. Se lee bajo
demanda: la historia no se copia a otro lado. Las funciones son sincrónicas (pyodbc) y
reciben una conexión a la base que se analiza.
"""

from collections import defaultdict
from datetime import datetime, timedelta

import pyodbc

VERSION_MINIMA = 13  # SQL Server 2016
# Marca dentro del texto de las consultas propias: Query Store guarda el texto de cada
# sentencia, así que las lecturas de la app se excluyen del ranking (y del de las DMVs).
MARCA = "/* dba-assistant:monitor */"
MAXIMO_CONSULTAS = 25
LARGO_TEXTO = 4000

# Métricas del ranking → expresión acumulada (CPU y duración en microsegundos).
METRICAS = {
    "cpu": "SUM(s.cpu)",
    "duracion": "SUM(s.duracion)",
    "lecturas": "SUM(s.lecturas)",
    "ejecuciones": "SUM(s.ejecuciones)",
}
PERIODOS_HORAS = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}
# Regresión: las últimas 24 h contra los 7 días anteriores, por ejecución.
HORAS_RECIENTES = 24
HORAS_REFERENCIA = 7 * 24
FACTOR_REGRESION = 1.5
MINIMO_EJECUCIONES = 10
FORMATO_HORA = "%Y-%m-%dT%H"

CONSULTA_BASES = """
SELECT name FROM sys.databases
WHERE database_id > 4 AND state_desc = 'ONLINE' AND source_database_id IS NULL
  AND HAS_DBACCESS(name) = 1
ORDER BY name
"""

CONSULTA_OPCIONES = f"""
SELECT {MARCA} actual_state_desc, desired_state_desc, readonly_reason,
       current_storage_size_mb, max_storage_size_mb
FROM sys.database_query_store_options
"""

# Estadísticas por consulta y plan en el período (los promedios de Query Store se
# multiplican por las ejecuciones para poder sumarlos).
_ESTADISTICAS = f"""
WITH s AS (
    SELECT {MARCA} p.query_id, rs.plan_id, rs.count_executions AS ejecuciones,
           rs.avg_cpu_time * rs.count_executions AS cpu,
           rs.avg_duration * rs.count_executions AS duracion,
           rs.avg_logical_io_reads * rs.count_executions AS lecturas,
           CONVERT(VARCHAR(13), i.start_time AT TIME ZONE 'UTC', 126) AS hora
    FROM sys.query_store_runtime_stats AS rs
    JOIN sys.query_store_runtime_stats_interval AS i
        ON i.runtime_stats_interval_id = rs.runtime_stats_interval_id
    JOIN sys.query_store_plan AS p ON p.plan_id = rs.plan_id
    WHERE i.start_time >= DATEADD(HOUR, -?, SYSDATETIMEOFFSET())
)
"""

CONSULTA_SERIE = (
    _ESTADISTICAS
    + """
SELECT s.query_id, s.plan_id, s.hora, SUM(s.ejecuciones), SUM(s.cpu), SUM(s.duracion),
       SUM(s.lecturas)
FROM s
WHERE s.query_id IN ({ids})
GROUP BY s.query_id, s.plan_id, s.hora
"""
)

CONSULTA_TEXTOS = f"""
SELECT {MARCA} q.query_id, qt.query_sql_text, OBJECT_SCHEMA_NAME(q.object_id),
       OBJECT_NAME(q.object_id)
FROM sys.query_store_query AS q
JOIN sys.query_store_query_text AS qt ON qt.query_text_id = q.query_text_id
WHERE q.query_id IN ({{ids}})
"""

CONSULTA_PLANES = f"""
SELECT {MARCA} plan_id, is_forced_plan, CONVERT(VARCHAR(19), initial_compile_start_time, 126),
       CONVERT(VARCHAR(19), last_execution_time, 126)
FROM sys.query_store_plan
WHERE query_id = ?
ORDER BY plan_id
"""


def version(cursor: pyodbc.Cursor) -> int:
    return int(cursor.execute("SELECT SERVERPROPERTY('ProductMajorVersion')").fetchone()[0])


def bases(cursor: pyodbc.Cursor) -> list[str]:
    return [f[0] for f in cursor.execute(CONSULTA_BASES).fetchall()]


def opciones(cursor: pyodbc.Cursor) -> dict:
    """Estado del Query Store de la base de la conexión."""
    fila = cursor.execute(CONSULTA_OPCIONES).fetchone()
    return {
        "estado": fila[0],
        "deseado": fila[1],
        "motivo_solo_lectura": fila[2],
        "usado_mb": int(fila[3] or 0),
        "maximo_mb": int(fila[4] or 0),
    }


def _por_ejecucion(total: float, ejecuciones: int) -> float | None:
    return total / ejecuciones if ejecuciones else None


def _valor(metrica: str, fila: dict) -> float:
    """Valor de la métrica en un tramo: ms para CPU y duración."""
    if metrica == "ejecuciones":
        return fila["ejecuciones"]
    if metrica == "lecturas":
        return fila["lecturas"]
    return fila[metrica] / 1000


def _serie(cursor: pyodbc.Cursor, ids: list[int], horas: int) -> dict[int, list[dict]]:
    if not ids:
        return {}
    filas = cursor.execute(
        CONSULTA_SERIE.format(ids=",".join(str(int(i)) for i in ids)), horas
    ).fetchall()
    resultado: dict[int, list[dict]] = defaultdict(list)
    for query_id, plan_id, hora, ejecuciones, cpu, duracion, lecturas in filas:
        resultado[query_id].append(
            {
                "plan_id": plan_id,
                "hora": hora,
                "ejecuciones": int(ejecuciones or 0),
                "cpu": float(cpu or 0),
                "duracion": float(duracion or 0),
                "lecturas": float(lecturas or 0),
            }
        )
    return resultado


def _sumar(filas: list[dict]) -> dict:
    total = {"ejecuciones": 0, "cpu": 0.0, "duracion": 0.0, "lecturas": 0.0}
    for f in filas:
        for clave in total:
            total[clave] += f[clave]
    return total


def tendencia(filas: list[dict], ahora: str, metrica: str) -> dict:
    """Marcas de una consulta a partir de sus tramos horarios (``ahora``: hora UTC actual
    en formato ``YYYY-MM-DDTHH``).

    - empeoró: en las últimas 24 h, por ejecución, supera en más de un 50% el promedio de
      los 7 días anteriores (con un mínimo de ejecuciones en ambos períodos);
    - cambió el plan: apareció en las últimas 24 h un plan que no se usaba antes.
    """
    # Para "ejecuciones" la regresión se mide por duración: lo que nota el usuario.
    medida = "duracion" if metrica == "ejecuciones" else metrica
    limite = _restar_horas(ahora, HORAS_RECIENTES)
    recientes = [f for f in filas if f["hora"] >= limite]
    anteriores = [f for f in filas if f["hora"] < limite]
    reciente, anterior = _sumar(recientes), _sumar(anteriores)
    valor_reciente = _por_ejecucion(_valor(medida, reciente), reciente["ejecuciones"])
    valor_anterior = _por_ejecucion(_valor(medida, anterior), anterior["ejecuciones"])
    empeoro = (
        reciente["ejecuciones"] >= MINIMO_EJECUCIONES
        and anterior["ejecuciones"] >= MINIMO_EJECUCIONES
        and bool(valor_anterior)
        and valor_reciente > FACTOR_REGRESION * valor_anterior
    )
    planes_previos = {f["plan_id"] for f in anteriores}
    cambio_plan = bool(planes_previos) and any(
        f["plan_id"] not in planes_previos for f in recientes
    )
    return {
        "empeoro": empeoro,
        "cambio_plan": cambio_plan,
        "medida": medida,
        "antes": round(valor_anterior, 2) if valor_anterior is not None else None,
        "ahora": round(valor_reciente, 2) if valor_reciente is not None else None,
    }


def _restar_horas(hora: str, horas: int) -> str:
    return (datetime.strptime(hora, FORMATO_HORA) - timedelta(hours=horas)).strftime(FORMATO_HORA)


def _redondear(valor: float | None) -> float | None:
    return round(valor, 2) if valor is not None else None


def _puntos(filas: list[dict], metrica: str, desde: str, por_dia: bool) -> list[dict]:
    """Serie para el gráfico: valor por ejecución (o ejecuciones) por hora o por día."""
    tramos: dict[str, list[dict]] = defaultdict(list)
    for f in filas:
        if f["hora"] >= desde:
            tramos[f["hora"][:10] if por_dia else f["hora"]].append(f)
    puntos = []
    for clave in sorted(tramos):
        total = _sumar(tramos[clave])
        valor = (
            total["ejecuciones"]
            if metrica == "ejecuciones"
            else _por_ejecucion(_valor(metrica, total), total["ejecuciones"])
        )
        puntos.append({"t": clave, "v": round(valor, 2) if valor is not None else None})
    return puntos


def _hora_actual(cursor: pyodbc.Cursor) -> str:
    return cursor.execute("SELECT CONVERT(VARCHAR(13), SYSUTCDATETIME(), 126)").fetchone()[0]


def _textos(cursor: pyodbc.Cursor, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    filas = cursor.execute(CONSULTA_TEXTOS.format(ids=",".join(str(int(i)) for i in ids)))
    return {
        f[0]: {
            "texto": (f[1] or "").strip()[:LARGO_TEXTO],
            "objeto": f"{f[2]}.{f[3]}" if f[3] else None,
        }
        for f in filas.fetchall()
    }


def ranking(cursor: pyodbc.Cursor, metrica: str, periodo: str) -> list[dict]:
    """Las consultas que más consumen en el período, con totales, serie y marcas."""
    horas = PERIODOS_HORAS[periodo]
    orden = METRICAS[metrica]
    filas = cursor.execute(
        _ESTADISTICAS
        + f"""
SELECT TOP ({MAXIMO_CONSULTAS}) s.query_id, SUM(s.ejecuciones), SUM(s.cpu), SUM(s.duracion),
       SUM(s.lecturas), COUNT(DISTINCT s.plan_id)
FROM s
JOIN sys.query_store_query AS q ON q.query_id = s.query_id
JOIN sys.query_store_query_text AS qt ON qt.query_text_id = q.query_text_id
WHERE qt.query_sql_text NOT LIKE N'%dba-assistant:monitor%'
GROUP BY s.query_id
ORDER BY {orden} DESC
""",
        horas,
    ).fetchall()
    ids = [f[0] for f in filas]
    ahora = _hora_actual(cursor)
    # La tendencia necesita los 8 días aunque el período elegido sea más corto.
    serie = _serie(cursor, ids, max(horas, HORAS_RECIENTES + HORAS_REFERENCIA))
    textos = _textos(cursor, ids)
    desde = _restar_horas(ahora, horas)
    consultas = []
    for query_id, ejecuciones, cpu, duracion, lecturas, planes in filas:
        ejecuciones = int(ejecuciones or 0)
        consultas.append(
            {
                "query_id": query_id,
                **textos.get(query_id, {"texto": "", "objeto": None}),
                "ejecuciones": ejecuciones,
                "cpu_ms": round(float(cpu or 0) / 1000, 1),
                "duracion_ms": round(float(duracion or 0) / 1000, 1),
                "lecturas": int(lecturas or 0),
                "cpu_ms_ejecucion": round(float(cpu or 0) / 1000 / ejecuciones, 2)
                if ejecuciones
                else None,
                "duracion_ms_ejecucion": round(float(duracion or 0) / 1000 / ejecuciones, 2)
                if ejecuciones
                else None,
                "planes": planes,
                "tendencia": tendencia(serie.get(query_id, []), ahora, metrica),
                "serie": _puntos(serie.get(query_id, []), metrica, desde, horas > 48),
            }
        )
    return consultas


def detalle(cursor: pyodbc.Cursor, query_id: int, metrica: str, periodo: str) -> dict | None:
    """Texto completo, planes y serie por plan de una consulta."""
    textos = _textos(cursor, [query_id])
    if query_id not in textos:
        return None
    horas = PERIODOS_HORAS[periodo]
    ahora = _hora_actual(cursor)
    filas = _serie(cursor, [query_id], max(horas, HORAS_RECIENTES + HORAS_REFERENCIA)).get(
        query_id, []
    )
    desde = _restar_horas(ahora, horas)
    planes = []
    for plan_id, forzado, compilado, ultima in cursor.execute(CONSULTA_PLANES, query_id).fetchall():
        propias = [f for f in filas if f["plan_id"] == plan_id]
        total = _sumar([f for f in propias if f["hora"] >= desde])
        planes.append(
            {
                "plan_id": plan_id,
                "forzado": bool(forzado),
                "compilado": compilado,
                "ultima_ejecucion": ultima,
                "ejecuciones": total["ejecuciones"],
                "valor": _redondear(
                    _por_ejecucion(_valor(metrica, total), total["ejecuciones"])
                    if metrica != "ejecuciones"
                    else total["ejecuciones"]
                ),
                "serie": _puntos(propias, metrica, desde, horas > 48),
            }
        )
    return {
        "query_id": query_id,
        **textos[query_id],
        "tendencia": tendencia(filas, ahora, metrica),
        "serie": _puntos(filas, metrica, desde, horas > 48),
        "planes": planes,
    }


def citar(nombre: str) -> str:
    return "[" + nombre.replace("]", "]]") + "]"


def estado_bases(conexion: pyodbc.Connection) -> dict:
    """Estado del Query Store en cada base de usuario de la instancia."""
    cursor = conexion.cursor()
    numero = version(cursor)
    if numero < VERSION_MINIMA:
        return {"version": numero, "disponible": False, "bases": []}
    resultado = []
    for base in bases(cursor):
        try:
            cursor.execute(f"USE {citar(base)}")
            resultado.append({"base": base, **opciones(cursor)})
        except pyodbc.Error:
            resultado.append({"base": base, "estado": "SIN_ACCESO"})
    cursor.execute("USE master")
    return {"version": numero, "disponible": True, "bases": resultado}


def consultas_de_base(conexion: pyodbc.Connection, metrica: str, periodo: str) -> dict:
    """Ranking de la base de la conexión, si su Query Store está activo."""
    cursor = conexion.cursor()
    if version(cursor) < VERSION_MINIMA:
        return {"estado": "NO_DISPONIBLE", "consultas": []}
    estado = opciones(cursor)
    if estado["estado"] not in ("READ_WRITE", "READ_ONLY"):
        return {**estado, "consultas": []}
    return {**estado, "consultas": ranking(cursor, metrica, periodo)}
