"""Persistencia del monitoreo.

- Umbrales: base SQL Server del tenant (estructura funcional).
- Serie de métricas: colección time-series ``metricas`` de la base MongoDB del tenant.
- Última lectura de cada instancia: Redis, para servir el dashboard sin consultar el motor.
"""

import json
from datetime import UTC, datetime

from app.core.config import obtener_configuracion
from app.datos import cache, mongo, sqlserver
from app.modulos.monitor.entidades import Umbral

# --- Umbrales (SQL Server) ----------------------------------------------------------


def listar_umbrales(base: str, tenant_id: str, conexion_id: str) -> dict[str, Umbral]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """SELECT metrica, habilitada, warning, critical FROM dbo.umbral_metrica
                   WHERE tenant_id = ? AND conexion_id = ?""",
                tenant_id,
                conexion_id,
            )
            .fetchall()
        )
    return {f.metrica: Umbral(f.metrica, bool(f.habilitada), f.warning, f.critical) for f in filas}


def listar_umbrales_del_tenant(base: str, tenant_id: str) -> dict[str, dict[str, Umbral]]:
    """Configuración de todas las conexiones: {conexion_id: {metrica: Umbral}}."""
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """SELECT conexion_id, metrica, habilitada, warning, critical
                   FROM dbo.umbral_metrica WHERE tenant_id = ?""",
                tenant_id,
            )
            .fetchall()
        )
    resultado: dict[str, dict[str, Umbral]] = {}
    for f in filas:
        resultado.setdefault(str(f.conexion_id), {})[f.metrica] = Umbral(
            f.metrica, bool(f.habilitada), f.warning, f.critical
        )
    return resultado


def guardar_umbrales(base: str, tenant_id: str, conexion_id: str, umbrales: list[Umbral]) -> None:
    """Reemplaza la configuración completa de la conexión en una sola transacción."""
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            "DELETE FROM dbo.umbral_metrica WHERE tenant_id = ? AND conexion_id = ?",
            tenant_id,
            conexion_id,
        )
        for u in umbrales:
            cursor.execute(
                """INSERT INTO dbo.umbral_metrica
                       (tenant_id, conexion_id, metrica, habilitada, warning, critical)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                tenant_id,
                conexion_id,
                u.metrica,
                u.habilitada,
                u.warning,
                u.critical,
            )
        conexion.commit()


# --- Serie de métricas (MongoDB) ----------------------------------------------------


async def insertar_muestra(
    base_mongodb: str, conexion_id: str, ts: datetime, valores: dict[str, float | None]
) -> None:
    documento = {k: v for k, v in valores.items() if v is not None}
    await mongo.base(base_mongodb)[mongo.COLECCION_METRICAS].insert_one(
        {"ts": ts, "meta": {"conexion_id": conexion_id}, **documento}
    )


async def serie(
    base_mongodb: str, conexion_id: str, metricas: list[str], desde: datetime, intervalo: int
) -> list[dict]:
    """Promedio de cada métrica por intervalo de ``intervalo`` segundos desde ``desde``."""
    tramos = mongo.base(base_mongodb)[mongo.COLECCION_METRICAS].aggregate(
        [
            {"$match": {"meta.conexion_id": conexion_id, "ts": {"$gte": desde}}},
            {
                "$group": {
                    "_id": {"$dateTrunc": {"date": "$ts", "unit": "second", "binSize": intervalo}},
                    **{m: {"$avg": f"${m}"} for m in metricas},
                }
            },
            {"$sort": {"_id": 1}},
        ]
    )
    return [
        {
            "ts": t["_id"].replace(tzinfo=UTC).isoformat(),
            **{m: round(t[m], 1) if t.get(m) is not None else None for m in metricas},
        }
        async for t in await tramos
    ]


# --- Última lectura (Redis) ---------------------------------------------------------


def _clave(tenant_id: str, conexion_id: str) -> str:
    return f"monitor:{tenant_id}:{conexion_id}"


async def guardar_estado(tenant_id: str, conexion_id: str, estado: dict) -> None:
    await cache.cliente().set(
        _clave(tenant_id, conexion_id),
        json.dumps(estado),
        ex=obtener_configuracion().cache_ttl_metricas,
    )


async def obtener_estado(tenant_id: str, conexion_id: str) -> dict | None:
    valor = await cache.cliente().get(_clave(tenant_id, conexion_id))
    return json.loads(valor) if valor else None


async def guardar_en_cache(clave: str, valor) -> None:
    await cache.cliente().set(
        clave, json.dumps(valor), ex=obtener_configuracion().cache_ttl_metricas
    )


async def obtener_de_cache(clave: str):
    valor = await cache.cliente().get(clave)
    return json.loads(valor) if valor else None
