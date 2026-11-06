"""Aviso de acceso por correo al usuario dado de alta (CU-001-000-A).

La app no maneja contraseñas: el correo le indica la dirección de su empresa y que entra
con Google o con su email (creando la contraseña en Auth0), siempre con segundo factor.
Sale en el idioma del usuario o, si todavía no tiene uno, en el de quien lo da de alta.
"""

import logging

from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.datos import correo
from app.identidad.entidades import Usuario
from app.tenants.entidades import Tenant
from app.web.i18n import en_idioma, formatear, idioma_actual, traducir

log = logging.getLogger(__name__)

ENVIADA, SIN_CORREO, FALLIDA = "enviada", "sin_correo", "fallida"


def direccion_de_acceso(tenant: Tenant) -> str:
    return f"https://{tenant.slug}.{obtener_configuracion().dominio_base}"


def armar(tenant: Tenant, usuario: Usuario, invitante: str | None) -> tuple[str, str]:
    """Asunto y texto del aviso, en el idioma del destinatario."""
    with en_idioma(usuario.idioma or idioma_actual()):
        ambientes = ", ".join(usuario.ambientes) or traducir("usuarios.invitacion.sin_ambientes")
        asunto = formatear("usuarios.invitacion.asunto", empresa=tenant.nombre)
        texto = formatear(
            "usuarios.invitacion.cuerpo",
            nombre=usuario.nombre or usuario.email,
            invitante=invitante or traducir("app.nombre"),
            empresa=tenant.nombre,
            rol=traducir(f"rol.{usuario.rol}"),
            ambientes=ambientes,
            url=direccion_de_acceso(tenant),
            email=usuario.email,
        )
    return asunto, texto


async def enviar(tenant: Tenant, usuario: Usuario, invitante: str | None) -> str:
    """Envía el aviso. Devuelve enviada, sin_correo (SMTP no configurado) o fallida."""
    if not correo.configurado():
        return SIN_CORREO
    asunto, texto = armar(tenant, usuario, invitante)
    try:
        await run_in_threadpool(correo.enviar, [usuario.email], asunto, texto)
    except Exception:
        log.exception("NTF_EML_000 aviso de acceso a %s", usuario.email)
        return FALLIDA
    return ENVIADA
