"""Correos de la plataforma: invitación a registrar una empresa, código de verificación y
aviso de acceso a un operador nuevo. Salen en el idioma de quien los dispara."""

import logging

from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.datos import correo
from app.tenants.entidades import SUBDOMINIO_PLATAFORMA, SUBDOMINIO_REGISTRO
from app.web.i18n import formatear

log = logging.getLogger(__name__)

ENVIADO, SIN_CORREO, FALLIDO = "enviado", "sin_correo", "fallido"
RUTA_REGISTRO = "/registra-tu-empresa"


def direccion_plataforma() -> str:
    return f"https://{SUBDOMINIO_PLATAFORMA}.{obtener_configuracion().dominio_base}"


def direccion_registro() -> str:
    return f"https://{SUBDOMINIO_REGISTRO}.{obtener_configuracion().dominio_base}{RUTA_REGISTRO}"


def direccion_empresa(slug: str) -> str:
    return f"https://{slug}.{obtener_configuracion().dominio_base}"


async def _enviar(destinatario: str, asunto: str, texto: str) -> str:
    if not correo.configurado():
        return SIN_CORREO
    try:
        await run_in_threadpool(correo.enviar, [destinatario], asunto, texto)
    except Exception:
        log.exception("NTF_EML_000 correo de plataforma a %s", destinatario)
        return FALLIDO
    return ENVIADO


async def invitacion(email: str, enlace: str, invitante: str) -> str:
    return await _enviar(
        email,
        formatear("plataforma.correo.invitacion.asunto"),
        formatear(
            "plataforma.correo.invitacion.cuerpo",
            email=email,
            enlace=enlace,
            invitante=invitante,
        ),
    )


async def codigo(email: str, valor: str) -> str:
    return await _enviar(
        email,
        formatear("plataforma.correo.codigo.asunto", codigo=valor),
        formatear("plataforma.correo.codigo.cuerpo", codigo=valor),
    )


async def operador(email: str, invitante: str) -> str:
    return await _enviar(
        email,
        formatear("plataforma.correo.operador.asunto"),
        formatear(
            "plataforma.correo.operador.cuerpo",
            email=email,
            invitante=invitante,
            url=direccion_plataforma(),
        ),
    )
