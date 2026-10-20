"""Notificación de alertas por los canales configurados: push (Web Push) y correo.

Una falla de un canal se registra y no impide el otro: la alerta ya quedó guardada.
"""

import logging

from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.datos import correo
from app.identidad import repositorios as usuarios
from app.modulos.alertas.entidades import Alerta, Condicion
from app.modulos.web_push import servicios as push
from app.tenants.entidades import Tenant
from app.web.i18n import traducir

log = logging.getLogger(__name__)


def url_alerta(tenant: Tenant, alerta: Alerta) -> str:
    return f"https://{tenant.slug}.{obtener_configuracion().dominio_base}/alertas?id={alerta.id}"


def _texto_correo(tenant: Tenant, a: Alerta) -> str:
    return traducir("alertas.correo.cuerpo").format(
        titulo=a.titulo,
        descripcion=a.descripcion,
        severidad=traducir(f"alertas.severidad.{a.severidad}"),
        instancia=a.conexion_alias,
        servidor=a.servidor,
        ambiente=a.ambiente,
        url=url_alerta(tenant, a),
        empresa=tenant.nombre,
    )


async def avisar(
    tenant: Tenant, roles: tuple[str, ...], titulo: str, cuerpo: str, ruta: str
) -> None:
    """Aviso operativo (no una alerta) por push y correo a los usuarios con esos roles."""
    try:
        await push.enviar(tenant, roles, {"titulo": titulo, "cuerpo": cuerpo, "url": ruta})
    except Exception:
        log.exception("NTF_PSH_000 aviso %s", titulo)
    if not correo.configurado():
        return
    try:
        destinatarios = await run_in_threadpool(
            usuarios.emails_por_roles, tenant.base_sqlserver, str(tenant.id), roles
        )
        if destinatarios:
            url = f"https://{tenant.slug}.{obtener_configuracion().dominio_base}{ruta}"
            await run_in_threadpool(
                correo.enviar, destinatarios, titulo, f"{cuerpo}\n\n{url}\n\n— DBA Assistant"
            )
    except Exception:
        log.exception("NTF_EML_000 aviso %s", titulo)


async def notificar(tenant: Tenant, a: Alerta, c: Condicion) -> None:
    if c.push:
        try:
            await push.enviar(
                tenant,
                c.destinatarios,
                {
                    "titulo": f"{a.severidad.upper()} · {a.conexion_alias}",
                    "cuerpo": a.titulo,
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
            if destinatarios:
                asunto = traducir("alertas.correo.asunto").format(
                    severidad=a.severidad.upper(), instancia=a.conexion_alias, titulo=a.titulo
                )
                await run_in_threadpool(
                    correo.enviar, destinatarios, asunto, _texto_correo(tenant, a)
                )
        except Exception:
            log.exception("NTF_EML_000 alerta %s", a.id)
