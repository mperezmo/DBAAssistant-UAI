"""Traducción de lenguaje natural a T-SQL con la API de Claude (Anthropic).

El esquema y el contexto de negocio van en un bloque de sistema cacheado (prompt caching):
cambian poco y son la mayor parte de cada pedido. La respuesta se pide como salida
estructurada para no depender de interpretar texto libre. Las traducciones se guardan en
Redis (``cache_ttl_ia``) con una clave que incluye el contexto, así que cualquier cambio
de esquema o de contexto genera traducciones nuevas.
"""

import hashlib
import json
import logging
from functools import lru_cache

import anthropic
from pydantic import BaseModel, Field

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.core.secretos import SecretoNoEncontradoError, proveedor_secretos
from app.datos import cache

log = logging.getLogger(__name__)

MAXIMO_TOKENS = 4000
TIMEOUT_SEGUNDOS = 30

INSTRUCCIONES = """\
Sos el motor de traducción de DBA Assistant: convertís pedidos en lenguaje natural \
(español rioplatense, a veces en criollo) en una única sentencia T-SQL para Microsoft \
SQL Server.

Reglas:
- Usá solo bases, tablas, vistas y columnas que figuran en el esquema de la instancia. \
Para preguntas sobre el estado del motor podés usar las vistas de sistema (sys.dm_*, \
sys.databases, etc.).
- Elegí vos la base sobre la que corre la consulta y devolvela en "base". Si hacen falta \
tablas de otras bases, usá nombres de tres partes (base.esquema.tabla).
- Respetá el glosario de negocio: cada término equivale a la entidad y la definición \
indicadas. Armá los JOIN con las claves foráneas del esquema.
- Devolvé una sola sentencia, sin GO ni varias sentencias encadenadas.
- Preferí consultas de lectura. Generá INSERT, UPDATE o DELETE solo si el pedido es \
claramente modificar datos, y siempre con un WHERE que acote las filas. Si el pedido \
requiere cambios de estructura (CREATE, ALTER, DROP, índices), generá la sentencia: el \
sistema la deriva al Sandbox.
- No agregues TOP salvo que el pedido indique una cantidad; el sistema limita lo que \
muestra.
- Tené en cuenta las reglas de negocio y las tablas sensibles: si el pedido las \
contradice, explicalo.
- Si el pedido es ambiguo o no se puede resolver con el esquema, dejá "sql" vacío y en \
"explicacion" pedí la aclaración que falta.
- "explicacion": breve, en español rioplatense, qué hace la consulta y por qué elegiste \
esa base. "confianza": de 0 a 100, qué tan seguro estás de haber interpretado el pedido.\
"""


class Traduccion(BaseModel):
    sql: str | None = Field(description="Sentencia T-SQL, o null si falta información.")
    base: str | None = Field(description="Base sobre la que se ejecuta.")
    explicacion: str
    confianza: int = Field(ge=0, le=100)


@lru_cache
def _cliente() -> anthropic.AsyncAnthropic:
    try:
        clave = proveedor_secretos().obtener("anthropic_api_key")
    except SecretoNoEncontradoError as error:
        raise ErrorDominio("IA_API_000", str(error)) from error
    return anthropic.AsyncAnthropic(api_key=clave, timeout=TIMEOUT_SEGUNDOS, max_retries=1)


def _clave_cache(modelo: str, contexto: str, historial: list[dict], pedido: str) -> str:
    huella = hashlib.sha256(
        json.dumps([modelo, contexto, historial, pedido], ensure_ascii=False).encode()
    ).hexdigest()
    return f"ia:traduccion:{huella}"


async def traducir(contexto: str, historial: list[dict], pedido: str) -> Traduccion:
    """``historial``: turnos previos del hilo como mensajes user/assistant de texto."""
    cfg = obtener_configuracion()
    clave = _clave_cache(cfg.ia_modelo, contexto, historial, pedido)
    try:
        if guardada := await cache.cliente().get(clave):
            return Traduccion.model_validate_json(guardada)
    except Exception as error:
        log.warning("Caché de IA no disponible: %s", error)

    try:
        respuesta = await _cliente().messages.parse(
            model=cfg.ia_modelo,
            max_tokens=MAXIMO_TOKENS,
            system=[
                {"type": "text", "text": INSTRUCCIONES},
                {"type": "text", "text": contexto, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[*historial, {"role": "user", "content": pedido}],
            output_format=Traduccion,
            output_config={"effort": cfg.ia_esfuerzo},
        )
    except anthropic.APIConnectionError as error:
        raise ErrorDominio("IA_API_000", f"conexión: {error}") from error
    except anthropic.RateLimitError as error:
        raise ErrorDominio("IA_API_000", "límite de uso de la API") from error
    except anthropic.APIStatusError as error:
        raise ErrorDominio("IA_API_000", f"{error.status_code}: {error.message}") from error

    if respuesta.stop_reason == "refusal" or respuesta.parsed_output is None:
        raise ErrorDominio("IA_TRA_000", f"stop_reason={respuesta.stop_reason}")
    traduccion = respuesta.parsed_output
    log.info(
        "Traducción IA: %s tokens de entrada (%s desde caché), %s de salida",
        respuesta.usage.input_tokens,
        respuesta.usage.cache_read_input_tokens,
        respuesta.usage.output_tokens,
    )
    try:
        await cache.cliente().set(clave, traduccion.model_dump_json(), ex=cfg.cache_ttl_ia)
    except Exception as error:
        log.warning("Caché de IA no disponible: %s", error)
    return traduccion
