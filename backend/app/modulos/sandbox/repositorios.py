"""Persistencia del Sandbox: estado por servidor (SQL Server) y bitácora de ejecuciones
(MongoDB, colección ``bitacora_sandbox``, retenida 5 años como el resto de las bitácoras)."""

import json
from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId

from app.datos import mongo, sqlserver

COLECCION = "bitacora_sandbox"


def obtener(base: str, tenant_id: str, clave: str) -> dict | None:
    with sqlserver.conectar(base) as conexion:
        fila = (
            conexion.cursor()
            .execute(
                """SELECT clave, host, estado, sincronizado_en, bases, error, actualizado_en
                   FROM dbo.sandbox WHERE tenant_id = ? AND clave = ?""",
                tenant_id,
                clave,
            )
            .fetchone()
        )
    if fila is None:
        return None
    return {
        "clave": fila.clave,
        "host": fila.host,
        "estado": fila.estado,
        "sincronizado_en": fila.sincronizado_en,
        "bases": json.loads(fila.bases),
        "error": fila.error,
        "actualizado_en": fila.actualizado_en,
    }


def guardar(
    base: str,
    tenant_id: str,
    clave: str,
    host: str,
    estado: str,
    bases: list[dict] | None = None,
    error: str | None = None,
    sincronizado: bool = False,
) -> None:
    """Alta o actualización del estado. Sin ``bases`` conserva las de la última sincronización."""
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        actualizadas = cursor.execute(
            """UPDATE dbo.sandbox
               SET estado = ?, error = ?, bases = COALESCE(?, bases),
                   sincronizado_en = CASE WHEN ? = 1 THEN SYSUTCDATETIME() ELSE sincronizado_en END,
                   actualizado_en = SYSUTCDATETIME()
               WHERE tenant_id = ? AND clave = ?""",
            estado,
            error,
            json.dumps(bases) if bases is not None else None,
            sincronizado,
            tenant_id,
            clave,
        ).rowcount
        if not actualizadas:
            cursor.execute(
                """INSERT INTO dbo.sandbox (tenant_id, clave, host, estado, bases, error,
                       sincronizado_en)
                   VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ? = 1 THEN SYSUTCDATETIME() END)""",
                tenant_id,
                clave,
                host,
                estado,
                json.dumps(bases or []),
                error,
                sincronizado,
            )
        conexion.commit()


# --- Bitácora de Sandbox (CU-000-002-C) ---------------------------------------------


async def registrar(base_mongodb: str, documento: dict) -> str:
    resultado = await mongo.base(base_mongodb)[COLECCION].insert_one(
        {**documento, "ts": datetime.now(UTC)}
    )
    return str(resultado.inserted_id)


def _id(valor: str) -> ObjectId | None:
    try:
        return ObjectId(valor)
    except InvalidId, TypeError:
        return None


async def obtener_registro(base_mongodb: str, tenant_id: str, registro_id: str) -> dict | None:
    oid = _id(registro_id)
    if oid is None:
        return None
    return await mongo.base(base_mongodb)[COLECCION].find_one({"_id": oid, "tenant_id": tenant_id})


async def listar(base_mongodb: str, tenant_id: str, filtros: dict, limite: int = 50) -> list[dict]:
    consulta = {"tenant_id": tenant_id, **{k: v for k, v in filtros.items() if v}}
    cursor = (
        mongo.base(base_mongodb)[COLECCION]
        .find(consulta, {"plan_xml": 0, "muestra": 0})
        .sort("ts", -1)
        .limit(limite)
    )
    return [d async for d in cursor]


async def marcar_promovida(base_mongodb: str, registro_id: str, promocion: dict) -> None:
    await mongo.base(base_mongodb)[COLECCION].update_one(
        {"_id": ObjectId(registro_id)}, {"$set": {"promocion": promocion}}
    )
