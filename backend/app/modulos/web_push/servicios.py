"""Notificaciones push con el estándar Web Push (VAPID, W3C), sin servicios de terceros.

El navegador entrega una suscripción (endpoint del servicio push del fabricante y
claves de cifrado); el servidor firma cada envío con su clave VAPID.
"""

import base64
import json
import logging
from collections.abc import Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.core.secretos import SecretoNoEncontradoError, proveedor_secretos
from app.identidad.entidades import Sesion
from app.modulos.web_push import repositorios
from app.modulos.web_push.repositorios import Suscripcion
from app.tenants.entidades import Tenant
from app.web.i18n import en_idioma

log = logging.getLogger(__name__)

# Tiempo que el servicio push conserva el mensaje si el dispositivo está apagado.
VIDA_MENSAJE_SEGUNDOS = 3600
# Respuestas del servicio push que indican que la suscripción ya no existe.
SUSCRIPCION_VENCIDA = (404, 410)


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def generar_claves() -> tuple[str, str]:
    """Par de claves VAPID (P-256): (pública para el navegador, privada para el servidor)."""
    privada = ec.generate_private_key(ec.SECP256R1())
    publica = privada.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64(publica), _b64(privada.private_numbers().private_value.to_bytes(32, "big"))


def _clave_privada() -> str | None:
    try:
        return proveedor_secretos().obtener("vapid_clave_privada")
    except SecretoNoEncontradoError:
        return None


def configurado() -> bool:
    cfg = obtener_configuracion()
    return bool(cfg.vapid_clave_publica and cfg.vapid_contacto and _clave_privada())


def estado(tenant: Tenant, sesion: Sesion) -> dict:
    return {
        "habilitado": configurado(),
        "clave_publica": obtener_configuracion().vapid_clave_publica,
    }


async def suscribir(tenant: Tenant, sesion: Sesion, s: Suscripcion, agente: str | None) -> None:
    if not s.endpoint.startswith("https://") or not s.p256dh or not s.auth:
        raise ErrorDominio("USR_VAL_002", "Suscripción push inválida.")
    await run_in_threadpool(
        repositorios.guardar, tenant.base_sqlserver, str(tenant.id), sesion.usuario_id, s, agente
    )


async def desuscribir(tenant: Tenant, endpoint: str) -> None:
    await run_in_threadpool(repositorios.eliminar, tenant.base_sqlserver, str(tenant.id), endpoint)


def _enviar_uno(s: Suscripcion, datos: str) -> int | None:
    """Envía a un dispositivo. Devuelve el código HTTP si el servicio push lo rechazó."""
    cfg = obtener_configuracion()
    try:
        webpush(
            subscription_info={
                "endpoint": s.endpoint,
                "keys": {"p256dh": s.p256dh, "auth": s.auth},
            },
            data=datos,
            vapid_private_key=_clave_privada(),
            vapid_claims={"sub": cfg.vapid_contacto},
            ttl=VIDA_MENSAJE_SEGUNDOS,
            timeout=10,
        )
    except WebPushException as error:
        return error.response.status_code if error.response is not None else 0
    return None


async def enviar(tenant: Tenant, roles: tuple[str, ...], mensaje: dict | Callable[[], dict]) -> int:
    """Envía ``mensaje`` a los dispositivos de los usuarios con esos roles.

    Si ``mensaje`` es una función, se arma en el idioma de cada destinatario.

    Devuelve cuántos envíos se aceptaron. Las fallas se registran (NTF_PSH_000) sin
    interrumpir al llamador: una alerta no debe perderse porque falle el push.
    """
    if not configurado():
        return 0
    suscripciones = await run_in_threadpool(
        repositorios.listar_por_roles, tenant.base_sqlserver, str(tenant.id), roles
    )
    armados: dict[str, str] = {}
    aceptados = 0
    for s in suscripciones:
        with en_idioma(s.idioma) as idioma:
            if idioma not in armados:
                armados[idioma] = json.dumps(mensaje() if callable(mensaje) else mensaje)
        datos = armados[idioma]
        try:
            codigo = await run_in_threadpool(_enviar_uno, s, datos)
        except Exception as error:
            log.warning("NTF_PSH_000 %s: %s", s.endpoint[:60], error)
            continue
        if codigo is None:
            aceptados += 1
        elif codigo in SUSCRIPCION_VENCIDA:
            await desuscribir(tenant, s.endpoint)
        else:
            log.warning("NTF_PSH_000 %s: respuesta %s", s.endpoint[:60], codigo)
    return aceptados
