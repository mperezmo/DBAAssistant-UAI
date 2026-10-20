"""Hilos de conversación del chat en la base MongoDB del tenant.

Se guardan los pedidos, el T-SQL generado y un resumen de cada ejecución; nunca los datos
de los resultados.
"""

from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId

from app.datos import mongo

COLECCION = mongo.COLECCION_HILOS
MAXIMO_HILOS_LISTADOS = 50


def _id(valor: str) -> ObjectId | None:
    try:
        return ObjectId(valor)
    except InvalidId, TypeError:
        return None


def _fecha(valor: datetime) -> str:
    return valor.replace(tzinfo=UTC).isoformat()


def _salida(documento: dict) -> dict:
    return {
        **{k: v for k, v in documento.items() if k not in ("_id", "tenant_id")},
        "id": str(documento["_id"]),
        "creado_en": _fecha(documento["creado_en"]),
        "actualizado_en": _fecha(documento["actualizado_en"]),
    }


async def crear(
    base_mongodb: str, tenant_id: str, usuario_id: str, conexion_id: str, titulo: str
) -> str:
    ahora = datetime.now(UTC)
    resultado = await mongo.base(base_mongodb)[COLECCION].insert_one(
        {
            "tenant_id": tenant_id,
            "usuario_id": usuario_id,
            "conexion_id": conexion_id,
            "titulo": titulo,
            "mensajes": [],
            "creado_en": ahora,
            "actualizado_en": ahora,
        }
    )
    return str(resultado.inserted_id)


async def obtener(base_mongodb: str, tenant_id: str, usuario_id: str, hilo_id: str) -> dict | None:
    oid = _id(hilo_id)
    if oid is None:
        return None
    documento = await mongo.base(base_mongodb)[COLECCION].find_one(
        {"_id": oid, "tenant_id": tenant_id, "usuario_id": usuario_id}
    )
    return _salida(documento) if documento else None


async def listar(base_mongodb: str, tenant_id: str, usuario_id: str) -> list[dict]:
    cursor = (
        mongo.base(base_mongodb)[COLECCION]
        .find({"tenant_id": tenant_id, "usuario_id": usuario_id}, {"mensajes": 0})
        .sort("actualizado_en", -1)
        .limit(MAXIMO_HILOS_LISTADOS)
    )
    return [_salida(d) async for d in cursor]


async def agregar_mensajes(base_mongodb: str, hilo_id: str, mensajes: list[dict]) -> None:
    await mongo.base(base_mongodb)[COLECCION].update_one(
        {"_id": ObjectId(hilo_id)},
        {"$push": {"mensajes": {"$each": mensajes}}, "$set": {"actualizado_en": datetime.now(UTC)}},
    )


async def actualizar_mensaje(
    base_mongodb: str, hilo_id: str, mensaje_id: str, campos: dict
) -> None:
    await mongo.base(base_mongodb)[COLECCION].update_one(
        {"_id": ObjectId(hilo_id), "mensajes.id": mensaje_id},
        {
            "$set": {f"mensajes.$.{k}": v for k, v in campos.items()}
            | {"actualizado_en": datetime.now(UTC)}
        },
    )


async def eliminar(base_mongodb: str, tenant_id: str, usuario_id: str, hilo_id: str) -> bool:
    oid = _id(hilo_id)
    if oid is None:
        return False
    resultado = await mongo.base(base_mongodb)[COLECCION].delete_one(
        {"_id": oid, "tenant_id": tenant_id, "usuario_id": usuario_id}
    )
    return resultado.deleted_count == 1
