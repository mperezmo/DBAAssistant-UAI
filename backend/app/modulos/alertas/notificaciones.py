"""Notificación de alertas por los canales configurados: push (Web Push) y correo.

Una falla de un canal se registra y no impide el otro: la alerta ya quedó guardada.
"""

import logging
from collections.abc import Callable

from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.datos import correo
from app.identidad import repositorios as usuarios
from app.modulos.alertas.entidades import Alerta, Condicion
from app.modulos.web_push import servicios as push
from app.tenants.entidades import Tenant
from app.web.i18n import en_idioma, traducir

log = logging.getLogger(__name__)


def url_alerta(tenant: Tenant, alerta: Alerta) -> str:
    return f"https://{tenant.slug}.{obtener_configuracion().dominio_base}/alertas?id={alerta.id}"


def _texto_correo(tenant: Tenant, a: Alerta) -> str:
    return traducir("alertas.correo.cuerpo").format(
        titulo=a.titulo_visible,
        descripcion=a.descripcion_visible,
        severidad=traducir(f"alertas.severidad.{a.severidad}"),
        instancia=a.conexion_alias,
        servidor=a.servidor,
        ambiente=a.ambiente,
        url=url_alerta(tenant, a),
        empresa=tenant.nombre,
    )


def _por_idioma(destinatarios: list[tuple[str, str | None]]) -> dict[str, list[str]]:
    """Emails agrupados por idioma: un envío por idioma con su texto."""
    grupos: dict[str, list[str]] = {}
    for email, idioma in destinatarios:
        with en_idioma(idioma) as elegido:
            grupos.setdefault(elegido, []).append(email)
    return grupos


async def avisar(
    tenant: Tenant,
    roles: tuple[str, ...],
    titulo: Callable[[], str],
    cuerpo: Callable[[], str],
    ruta: str,
) -> None:
    """Aviso operativo (no una alerta) por push y correo a los usuarios con esos roles.

    Título y cuerpo se arman en el idioma de cada destinatario.
    """
    try:
        await push.enviar(
            tenant, roles, lambda: {"titulo": titulo(), "cuerpo": cuerpo(), "url": ruta}
        )
    except Exception:
        log.exception("NTF_PSH_000 aviso %s", ruta)
    if not correo.configurado():
        return
    try:
        destinatarios = await run_in_threadpool(
            usuarios.emails_por_roles, tenant.base_sqlserver, str(tenant.id), roles
        )
        url = f"https://{tenant.slug}.{obtener_configuracion().dominio_base}{ruta}"
        for idioma, emails in _por_idioma(destinatarios).items():
            with en_idioma(idioma):
                asunto, texto = titulo(), f"{cuerpo()}\n\n{url}\n\n— DBA Assistant"
            await run_in_threadpool(correo.enviar, emails, asunto, texto)
    except Exception:
        log.exception("NTF_EML_000 aviso %s", ruta)


async def notificar(tenant: Tenant, a: Alerta, c: Condicion) -> None:
    if c.push:
        try:
            await push.enviar(
                tenant,
                c.destinatarios,
                lambda: {
                    "titulo": f"{a.severidad.upper()} · {a.conexion_alias}",
                    "cuerpo": a.titulo_visible,
                    "url": f"/alertas?id={a.id}",
                    "etiqueta": f"alerta-{a.id}",
                },
            )
        except Exception:
            log.exception("NTF_PSH_000 alerta %s", a.id)

    if c.email and correo.configurado():
        try:
            destinatarios = await run_in_threadpool(
                usuarios.emails_por_roles, tenant.base_sqlserver, str(tenant.id), c.destinatarios
            )
            for idioma, emails in _por_idioma(destinatarios).items():
                with en_idioma(idioma):
                    asunto = traducir("alertas.correo.asunto").format(
                        severidad=a.severidad.upper(),
                        instancia=a.conexion_alias,
                        titulo=a.titulo_visible,
                    )
                    texto = _texto_correo(tenant, a)
                await run_in_threadpool(correo.enviar, emails, asunto, texto)
        except Exception:
            log.exception("NTF_EML_000 alerta %s", a.id)
