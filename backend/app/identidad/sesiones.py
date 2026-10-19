"""Sesiones del lado del servidor, guardadas en Redis.

El navegador solo recibe un identificador aleatorio en una cookie HttpOnly; los
datos de la sesión y los tokens de Auth0 nunca salen del servidor. La sesión dura
un tiempo fijo y no se renueva con la actividad.
"""

import json
import secrets
import time

from redis.asyncio import Redis

from app.datos import cache
from app.identidad.entidades import Sesion

COOKIE = "dba_sesion"
PREFIJO_SESION = "sesion:"
PREFIJO_INGRESO = "ingreso:"
VIGENCIA_INGRESO_SEGUNDOS = 600


def _redis() -> Redis:
    return cache.cliente()


async def crear(datos: dict, duracion_segundos: int, tokens: dict) -> Sesion:
    # El segundo factor del ingreso no habilita acciones sobre PROD: esas se confirman en
    # el momento (ver identidad/reautenticacion.py).
    sesion = Sesion(id=secrets.token_urlsafe(32), creada_en=time.time(), **datos)
    registro = {"sesion": sesion.a_dict(), "tokens": tokens}
    await _redis().set(PREFIJO_SESION + sesion.id, json.dumps(registro), ex=duracion_segundos)
    return sesion


async def obtener(sesion_id: str | None) -> Sesion | None:
    if not sesion_id:
        return None
    crudo = await _redis().get(PREFIJO_SESION + sesion_id)
    return Sesion(**json.loads(crudo)["sesion"]) if crudo else None


async def registrar_segundo_factor(sesion_id: str) -> None:
    """Marca que el usuario acaba de confirmar su identidad con el segundo factor."""
    clave = PREFIJO_SESION + sesion_id
    crudo = await _redis().get(clave)
    if not crudo:
        return
    registro = json.loads(crudo)
    registro["sesion"]["segundo_factor_en"] = time.time()
    # keepttl: la reautenticación no extiende la duración fija de la sesión.
    await _redis().set(clave, json.dumps(registro), keepttl=True)


async def eliminar(sesion_id: str) -> None:
    await _redis().delete(PREFIJO_SESION + sesion_id)


async def guardar_ingreso(state: str, datos: dict) -> None:
    """Guarda el contexto de un ingreso en curso (state, nonce, tenant)."""
    await _redis().set(PREFIJO_INGRESO + state, json.dumps(datos), ex=VIGENCIA_INGRESO_SEGUNDOS)


async def consumir_ingreso(state: str) -> dict | None:
    """Devuelve y elimina el contexto del ingreso: un state sirve una sola vez."""
    crudo = await _redis().getdel(PREFIJO_INGRESO + state)
    return json.loads(crudo) if crudo else None
