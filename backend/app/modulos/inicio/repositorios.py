"""Lecturas del resumen operativo: SQL Server del tenant, Redis y MongoDB."""

from datetime import datetime

from app.datos import mongo, sqlserver
from app.modulos.alertas.condiciones import CLAVE_INSTANCIA


def conexiones(base: str, tenant_id: str) -> list[dict]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                "SELECT id, ambiente, creado_en FROM dbo.conexion WHERE tenant_id = ?", tenant_id
            )
            .fetchall()
        )
    return [{"id": str(f.id), "ambiente": f.ambiente, "creado_en": f.creado_en} for f in filas]


def alertas_activas(base: str, tenant_id: str) -> list[dict]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """SELECT ambiente, severidad, COUNT(*) AS cantidad FROM dbo.alerta
                   WHERE tenant_id = ? AND estado = 'activa' GROUP BY ambiente, severidad""",
                tenant_id,
            )
            .fetchall()
        )
    return [
        {"ambiente": f.ambiente, "severidad": f.severidad, "cantidad": f.cantidad} for f in filas
    ]


def caidas(base: str, tenant_id: str, desde: datetime, hasta: datetime) -> list[dict]:
    """Períodos sin conexión (alertas "instancia sin conexión") que se superponen al rango.

    Las fechas son UTC sin zona, como las guarda SQL Server.
    """
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """SELECT conexion_id, disparada_en, normalizada_en FROM dbo.alerta
                   WHERE tenant_id = ? AND clave = ? AND disparada_en < ?
                     AND (normalizada_en IS NULL OR normalizada_en > ?)""",
                tenant_id,
                CLAVE_INSTANCIA,
                hasta,
                desde,
            )
            .fetchall()
        )
    return [
        {"conexion_id": str(f.conexion_id), "desde": f.disparada_en, "hasta": f.normalizada_en}
        for f in filas
    ]


def workarounds_propios(base: str, tenant_id: str) -> list[dict]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                """SELECT ambientes, creado_en FROM dbo.workaround
                   WHERE tenant_id = ? AND eliminado_en IS NULL""",
                tenant_id,
            )
            .fetchall()
        )
    return [{"ambientes": f.ambientes.split(","), "creado_en": f.creado_en} for f in filas]


async def latencia_p95(base_mongodb: str, conexion_ids: list[str], desde: datetime) -> float | None:
    """Percentil 95 de la latencia de las lecturas del recolector desde ``desde``."""
    if not conexion_ids:
        return None
    resultado = await mongo.base(base_mongodb)[mongo.COLECCION_METRICAS].aggregate(
        [
            {
                "$match": {
                    "meta.conexion_id": {"$in": conexion_ids},
                    "ts": {"$gte": desde},
                    "latencia": {"$ne": None},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "p95": {
                        "$percentile": {"input": "$latencia", "p": [0.95], "method": "approximate"}
                    },
                }
            },
        ]
    )
    async for fila in resultado:
        return fila["p95"][0]
    return None


async def sql_pendientes_de_promocion(
    base_mongodb: str, tenant_id: str, ambientes: list[str], desde: datetime
) -> list[tuple[str, str]]:
    """(T-SQL, base) de las validaciones exitosas en Sandbox que todavía no se promovieron."""
    cursor = mongo.base(base_mongodb)["bitacora_sandbox"].find(
        {
            "tenant_id": tenant_id,
            "estado": "ok",
            "promocion": None,
            "ambiente": {"$in": ambientes},
            "ts": {"$gte": desde},
        },
        {"sql": 1, "base": 1},
    )
    return [(d["sql"], d["base"]) async for d in cursor]
