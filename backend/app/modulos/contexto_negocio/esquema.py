"""Lectura del esquema de una instancia: bases, tablas, columnas, claves primarias y foráneas.

Alimenta el árbol de Contexto de Negocio, la validación del glosario y el contexto que se
envía al motor IA. Se guarda en Redis unos minutos (``cache_ttl_contexto``) porque leerlo
en cada pedido del chat sería costoso; si Redis no responde, se lee directo.
"""

import json
import logging

import pyodbc
from starlette.concurrency import run_in_threadpool

from app.core import cifrado
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.datos import cache, sqlserver
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import Conexion
from app.tenants.entidades import Tenant

log = logging.getLogger(__name__)

# Límites para que el contexto del motor IA no crezca sin control en instancias grandes.
MAXIMO_BASES = 30
MAXIMO_TABLAS_POR_BASE = 400

CONSULTA_BASES = """
    SELECT name FROM sys.databases
    WHERE database_id > 4 AND state_desc = 'ONLINE' AND HAS_DBACCESS(name) = 1
    ORDER BY name
"""
CONSULTA_COLUMNAS = """
    SELECT s.name, o.name, o.type, c.name, t.name, c.max_length, c.is_nullable
    FROM sys.objects o
    JOIN sys.schemas s ON s.schema_id = o.schema_id
    JOIN sys.columns c ON c.object_id = o.object_id
    JOIN sys.types t ON t.user_type_id = c.user_type_id
    WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0
    ORDER BY s.name, o.name, c.column_id
"""
CONSULTA_PK = """
    SELECT s.name, o.name, c.name
    FROM sys.indexes i
    JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
    JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
    JOIN sys.objects o ON o.object_id = i.object_id
    JOIN sys.schemas s ON s.schema_id = o.schema_id
    WHERE i.is_primary_key = 1 AND o.is_ms_shipped = 0
"""
CONSULTA_FK = """
    SELECT sp.name, op.name, cp.name, sr.name, orf.name, cr.name
    FROM sys.foreign_key_columns fk
    JOIN sys.objects op ON op.object_id = fk.parent_object_id
    JOIN sys.schemas sp ON sp.schema_id = op.schema_id
    JOIN sys.columns cp ON cp.object_id = fk.parent_object_id AND cp.column_id = fk.parent_column_id
    JOIN sys.objects orf ON orf.object_id = fk.referenced_object_id
    JOIN sys.schemas sr ON sr.schema_id = orf.schema_id
    JOIN sys.columns cr ON cr.object_id = fk.referenced_object_id
        AND cr.column_id = fk.referenced_column_id
"""


def _abrir(conexion: Conexion, base: str = sqlserver.BASE_SISTEMA):
    return sqlserver.conectar_externo(
        conexiones.servidor_de(conexion),
        conexion.usuario_sql,
        cifrado.descifrar(conexion.password_sql_cifrada),
        base,
    )


def _tipo(nombre: str, largo: int) -> str:
    if nombre in ("varchar", "char", "varbinary", "binary"):
        return f"{nombre}({'max' if largo == -1 else largo})"
    if nombre in ("nvarchar", "nchar"):
        return f"{nombre}({'max' if largo == -1 else largo // 2})"
    return nombre


def _leer_base(conexion: Conexion, base: str) -> list[dict]:
    with _abrir(conexion, base) as abierta:
        cursor = abierta.cursor()
        tablas: dict[str, dict] = {}
        for esquema, nombre, tipo, columna, tipo_dato, largo, nula in cursor.execute(
            CONSULTA_COLUMNAS
        ).fetchall():
            clave = f"{esquema}.{nombre}"
            if clave not in tablas:
                if len(tablas) >= MAXIMO_TABLAS_POR_BASE:
                    continue
                tablas[clave] = {
                    "esquema": esquema,
                    "nombre": nombre,
                    "vista": tipo.strip() == "V",
                    "columnas": [],
                    "pk": [],
                    "fk": [],
                }
            tablas[clave]["columnas"].append(
                {"nombre": columna, "tipo": _tipo(tipo_dato, largo), "nula": bool(nula)}
            )
        for esquema, nombre, columna in cursor.execute(CONSULTA_PK).fetchall():
            if (t := tablas.get(f"{esquema}.{nombre}")) is not None:
                t["pk"].append(columna)
        for ep, tp, cp, er, tr, cr in cursor.execute(CONSULTA_FK).fetchall():
            if (t := tablas.get(f"{ep}.{tp}")) is not None:
                t["fk"].append({"columna": cp, "referencia": f"{er}.{tr}.{cr}"})
    return list(tablas.values())


def leer(conexion: Conexion) -> dict:
    """Esquema de todas las bases de usuario accesibles de la instancia."""
    try:
        with _abrir(conexion) as abierta:
            bases = [f[0] for f in abierta.cursor().execute(CONSULTA_BASES).fetchall()]
    except pyodbc.Error as error:
        raise ErrorDominio("CON_DB_000", str(error)) from error
    resultado = {}
    for base in bases[:MAXIMO_BASES]:
        try:
            resultado[base] = _leer_base(conexion, base)
        except pyodbc.Error as error:
            # Una base sin permisos no impide usar las demás.
            log.warning("No se pudo leer el esquema de %s en %s: %s", base, conexion.alias, error)
    return resultado


def _clave_cache(tenant: Tenant, conexion_id: str) -> str:
    return f"contexto:esquema:{tenant.id}:{conexion_id}"


async def obtener(tenant: Tenant, conexion_id: str, refrescar: bool = False) -> dict:
    """Esquema de la conexión, desde la caché si está vigente."""
    conexion = await conexiones.obtener(tenant, conexion_id)
    clave = _clave_cache(tenant, conexion_id)
    if not refrescar:
        try:
            if guardado := await cache.cliente().get(clave):
                return json.loads(guardado)
        except Exception as error:
            log.warning("Caché de esquema no disponible: %s", error)
    esquema = await run_in_threadpool(leer, conexion)
    try:
        await cache.cliente().set(
            clave, json.dumps(esquema), ex=obtener_configuracion().cache_ttl_contexto
        )
    except Exception as error:
        log.warning("Caché de esquema no disponible: %s", error)
    return esquema


def buscar_tabla(esquema: dict, base: str, nombre: str) -> dict | None:
    """Busca ``esquema.tabla`` (o ``tabla``, en dbo) sin distinguir mayúsculas."""
    partes = nombre.split(".")
    esquema_tabla, tabla = (partes[0], partes[1]) if len(partes) == 2 else ("dbo", partes[-1])
    for b, tablas in esquema.items():
        if b.lower() != base.lower():
            continue
        for t in tablas:
            if (
                t["esquema"].lower() == esquema_tabla.lower()
                and t["nombre"].lower() == tabla.lower()
            ):
                return t
    return None
