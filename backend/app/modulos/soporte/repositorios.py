"""Consultas de Soporte sobre la base propia de la aplicación.

SQL Server (base del tenant): estructura actual, migraciones aplicadas, historial de
cambios de estructura (``dbo.historial_ddl``) e historia de datos de las tablas
temporales. MongoDB (base del tenant): las versiones de la estructura, en la colección
``schema_aplicacion``, cada una con el snapshot completo.
"""

from datetime import UTC, datetime

from pymongo import DESCENDING

from app.datos import mongo, sqlserver
from app.modulos.historico_schema import captura
from app.modulos.soporte.entidades import (
    COLUMNA_DESDE,
    COLUMNA_HASTA,
    LIMITE_FILAS,
    PATRON_SENSIBLE,
    TablaConHistoria,
)

COLECCION = mongo.COLECCION_SCHEMA_APLICACION


def _q(nombre: str) -> str:
    return "[" + nombre.replace("]", "]]") + "]"


# --- Estructura -------------------------------------------------------------------


def capturar(base_sqlserver: str) -> dict[str, dict]:
    with sqlserver.conectar(base_sqlserver) as conexion:
        return captura.capturar(conexion)


def migraciones_aplicadas(base_sqlserver: str) -> list[str]:
    with sqlserver.conectar(base_sqlserver) as conexion:
        filas = (
            conexion.cursor()
            .execute("SELECT version FROM dbo.migracion_aplicada ORDER BY version")
            .fetchall()
        )
    return [fila[0] for fila in filas]


async def ultima_version(base_mongodb: str, tenant_id: str) -> dict | None:
    return await mongo.base(base_mongodb)[COLECCION].find_one(
        {"tenant_id": tenant_id}, sort=[("version", DESCENDING)]
    )


async def guardar_version(base_mongodb: str, documento: dict) -> int:
    """Guarda la versión siguiente del tenant y devuelve su número."""
    versiones = mongo.base(base_mongodb)[COLECCION]
    ultima = await versiones.find_one(
        {"tenant_id": documento["tenant_id"]}, {"version": 1}, sort=[("version", DESCENDING)]
    )
    numero = (ultima["version"] if ultima else 0) + 1
    await versiones.insert_one({**documento, "version": numero, "ts": datetime.now(UTC)})
    return numero


async def listar_versiones(base_mongodb: str, tenant_id: str) -> list[dict]:
    cursor = (
        mongo.base(base_mongodb)[COLECCION]
        .find(
            {"tenant_id": tenant_id},
            {"objetos": 0, "cambios.antes": 0, "cambios.despues": 0},
        )
        .sort("version", DESCENDING)
    )
    return [d async for d in cursor]


async def obtener_version(base_mongodb: str, tenant_id: str, version: int) -> dict | None:
    return await mongo.base(base_mongodb)[COLECCION].find_one(
        {"tenant_id": tenant_id, "version": version}
    )


# --- Historial de cambios de estructura (trigger DDL) ------------------------------

CAMPOS_DDL = "id, ts, evento, esquema, objeto, tipo_objeto, login, programa, equipo"


def historial_ddl(
    base_sqlserver: str, texto: str | None, salto: int, cantidad: int
) -> tuple[list[dict], int]:
    condicion, parametros = "", []
    if texto:
        condicion = "WHERE evento LIKE ? OR objeto LIKE ? OR login LIKE ? OR programa LIKE ?"
        patron = f"%{texto}%"
        parametros = [patron] * 4
    with sqlserver.conectar(base_sqlserver) as conexion:
        cursor = conexion.cursor()
        total = cursor.execute(
            f"SELECT COUNT(*) FROM dbo.historial_ddl {condicion}", *parametros
        ).fetchone()[0]
        cursor.execute(
            f"SELECT {CAMPOS_DDL} FROM dbo.historial_ddl {condicion}"
            " ORDER BY id DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY",
            *parametros,
            salto,
            cantidad,
        )
        columnas = [c[0] for c in cursor.description]
        filas = [dict(zip(columnas, fila, strict=True)) for fila in cursor.fetchall()]
    return filas, total


def ddl(base_sqlserver: str, ddl_id: int) -> dict | None:
    with sqlserver.conectar(base_sqlserver) as conexion:
        cursor = conexion.cursor()
        fila = cursor.execute(
            f"SELECT {CAMPOS_DDL}, tsql FROM dbo.historial_ddl WHERE id = ?", ddl_id
        ).fetchone()
        if fila is None:
            return None
        return dict(zip([c[0] for c in cursor.description], fila, strict=True))


# --- Historia de datos (tablas temporales) ----------------------------------------

CONSULTA_COLUMNAS = """
    SELECT c.name, CAST(CASE WHEN ic.column_id IS NULL THEN 0 ELSE 1 END AS BIT),
           ic.key_ordinal
    FROM sys.tables t
    JOIN sys.columns c ON c.object_id = t.object_id
    LEFT JOIN sys.indexes i ON i.object_id = t.object_id AND i.is_primary_key = 1
    LEFT JOIN sys.index_columns ic
        ON ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.column_id = c.column_id
    WHERE t.schema_id = SCHEMA_ID('dbo') AND t.name = ? AND t.temporal_type = 2
      AND c.generated_always_type = 0
    ORDER BY c.column_id
"""


def tabla_con_historia(base_sqlserver: str, tabla: str) -> TablaConHistoria | None:
    """Columnas visibles y clave de una tabla temporal; None si no lo es."""
    with sqlserver.conectar(base_sqlserver) as conexion:
        filas = conexion.cursor().execute(CONSULTA_COLUMNAS, tabla).fetchall()
    if not filas:
        return None
    clave = tuple(
        nombre for nombre, es_clave, _ in sorted(filas, key=lambda f: f[2] or 0) if es_clave
    )
    return TablaConHistoria(
        nombre=tabla,
        columnas=tuple(nombre for nombre, _, _ in filas if not PATRON_SENSIBLE.search(nombre)),
        clave=clave,
    )


def _consultar(
    base_sqlserver: str,
    tabla: TablaConHistoria,
    periodo: str,
    tenant_id: str,
    clave: str | None,
    orden: str,
    parametros: list,
) -> tuple[list[str], list[tuple]]:
    # El período va primero: es lo que se lee antes que nada en la historia de una fila.
    columnas = [COLUMNA_DESDE, COLUMNA_HASTA, *tabla.columnas]
    condiciones = ["tenant_id = ?"]
    parametros = [*parametros, tenant_id]
    if clave is not None and tabla.clave:
        # Se compara como texto: la clave puede ser un GUID, un número o un nombre.
        condiciones.append(f"CAST({_q(tabla.clave[0])} AS NVARCHAR(400)) = ?")
        parametros.append(clave)
    sql = (
        f"SELECT TOP ({LIMITE_FILAS}) {', '.join(_q(c) for c in columnas)}"
        f" FROM dbo.{_q(tabla.nombre)} FOR SYSTEM_TIME {periodo}"
        f" WHERE {' AND '.join(condiciones)} ORDER BY {orden}"
    )
    with sqlserver.conectar(base_sqlserver) as conexion:
        filas = conexion.cursor().execute(sql, *parametros).fetchall()
    return columnas, [tuple(f) for f in filas]


def filas_al_momento(
    base_sqlserver: str,
    tabla: TablaConHistoria,
    tenant_id: str,
    momento: datetime,
    clave: str | None,
) -> tuple[list[str], list[tuple]]:
    """La tabla tal como estaba en ``momento`` (UTC sin zona, como las columnas del período)."""
    orden = ", ".join(_q(c) for c in tabla.clave) or _q(COLUMNA_DESDE)
    return _consultar(base_sqlserver, tabla, "AS OF ?", tenant_id, clave, orden, [momento])


def cambios(
    base_sqlserver: str, tabla: TablaConHistoria, tenant_id: str, clave: str | None
) -> tuple[list[str], list[tuple]]:
    """Todas las versiones de las filas, de la más reciente a la más antigua."""
    orden = f"{_q(COLUMNA_DESDE)} DESC"
    return _consultar(base_sqlserver, tabla, "ALL", tenant_id, clave, orden, [])
