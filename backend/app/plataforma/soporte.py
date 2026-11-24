"""Acceso de soporte de un operador a una empresa.

Desde la consola, un operador que acaba de confirmar con el segundo factor pide un pase
de un solo uso (60 s) para una empresa. El subdominio de la empresa lo canjea por una
sesión de soporte de 1 hora que solo ve la vista acotada de Soporte: salud de los
servicios y errores de la aplicación, sin datos ni logs de la empresa, y sin acciones.

Cada acceso queda en la auditoría de la plataforma y en la de la empresa, y se avisa por
correo a los Administradores de la empresa.
"""

import json
import logging
import secrets

from starlette.concurrency import run_in_threadpool

from app.core.errores import ErrorDominio
from app.datos import cache, correo
from app.identidad import repositorios as usuarios
from app.identidad import sesiones
from app.identidad.entidades import ROL_OPERADOR, Sesion
from app.identidad.reautenticacion import exigir_segundo_factor_reciente
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.plataforma import correos, repositorios
from app.plataforma.servicios import registrar
from app.tenants import repositorios as tenants
from app.tenants.entidades import Tenant
from app.web.i18n import en_idioma, formatear

log = logging.getLogger(__name__)

PREFIJO_PASE = "soporte:pase:"
VIGENCIA_PASE_SEGUNDOS = 60
VIGENCIA_SESION_SEGUNDOS = 60 * 60


async def crear_pase(actor: Sesion, tenant_id: str) -> str:
    """Devuelve la URL de la empresa con el pase; exige el segundo factor reciente."""
    exigir_segundo_factor_reciente(actor)
    tenant = await run_in_threadpool(tenants.obtener_por_id, tenant_id)
    if tenant is None:
        raise ErrorDominio("TEN_VAL_000", tenant_id)
    if tenant.estado != "activo":
        raise ErrorDominio("TEN_VAL_003", tenant.slug)
    pase = secrets.token_urlsafe(32)
    await cache.cliente().set(
        PREFIJO_PASE + pase,
        json.dumps({"operador_id": actor.usuario_id, "tenant_id": str(tenant.id)}),
        ex=VIGENCIA_PASE_SEGUNDOS,
    )
    await registrar("plataforma.soporte_pase", actor.email, slug=tenant.slug)
    return f"{correos.direccion_empresa(tenant.slug)}/auth/soporte?pase={pase}"


async def canjear_pase(tenant: Tenant, pase: str) -> Sesion:
    """Abre la sesión de soporte en la empresa; el pase sirve una sola vez."""
    crudo = await cache.cliente().getdel(PREFIJO_PASE + (pase or ""))
    datos = json.loads(crudo) if crudo else None
    if datos is None or datos["tenant_id"] != str(tenant.id):
        raise ErrorDominio("USR_AUT_004", "Pase de soporte inválido o vencido.")
    if tenant.estado != "activo":
        raise ErrorDominio("TEN_VAL_003", tenant.slug)
    operador = await run_in_threadpool(repositorios.obtener_operador, datos["operador_id"])
    if operador is None or not operador.puede_ingresar:
        raise ErrorDominio("PLA_PER_000", datos["operador_id"])

    sesion = await sesiones.crear(
        {
            "usuario_id": operador.id,
            "tenant_id": str(tenant.id),
            "tenant_slug": tenant.slug,
            "email": operador.email,
            "nombre": operador.nombre or operador.email,
            "rol": ROL_OPERADOR,
        },
        VIGENCIA_SESION_SEGUNDOS,
        tokens={},
    )
    minutos = VIGENCIA_SESION_SEGUNDOS // 60
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="soporte.acceso_operador",
            usuario=operador.email,
            tenant_id=str(tenant.id),
            detalle={"rol": ROL_OPERADOR, "minutos": minutos, "solo_lectura": True},
        ),
    )
    await registrar("plataforma.soporte_acceso", operador.email, slug=tenant.slug)
    await _avisar_administradores(tenant, operador.nombre or operador.email, minutos)
    return sesion


async def _avisar_administradores(tenant: Tenant, operador: str, minutos: int) -> None:
    """Correo a los Administradores de la empresa, cada uno en su idioma."""
    if not correo.configurado():
        return
    destinatarios = await run_in_threadpool(
        usuarios.emails_por_roles, tenant.base_sqlserver, str(tenant.id), ("ADMINISTRADOR",)
    )
    for email, idioma in destinatarios:
        with en_idioma(idioma):
            asunto = formatear("soporte.operador.correo.asunto", empresa=tenant.nombre)
            texto = formatear(
                "soporte.operador.correo.cuerpo",
                operador=operador,
                empresa=tenant.nombre,
                minutos=minutos,
            )
        try:
            await run_in_threadpool(correo.enviar, [email], asunto, texto)
        except Exception:
            log.exception("NTF_EML_000 aviso de acceso de soporte a %s", email)


async def cerrar(tenant: Tenant, sesion: Sesion) -> None:
    await sesiones.eliminar(sesion.id)
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="soporte.salida_operador", usuario=sesion.email, tenant_id=str(tenant.id)
        ),
    )
