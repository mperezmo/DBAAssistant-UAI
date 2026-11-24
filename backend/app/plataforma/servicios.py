"""Consola de la plataforma: empresas, invitaciones y operadores.

Los operadores administran la plataforma, no los datos de cada empresa: ninguna operación
de este módulo abre la base de un tenant.
"""

import logging
from urllib.parse import urlencode

from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad.auth0 import ClienteAuth0
from app.identidad.entidades import Sesion
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.plataforma import correos, repositorios
from app.plataforma.entidades import (
    TENANT_PLATAFORMA,
    VIGENCIA_INVITACION,
    Invitacion,
    Operador,
    ahora_utc,
)
from app.tenants import repositorios as tenants
from app.tenants import resolucion
from app.tenants import servicios as tenants_servicios
from app.tenants.entidades import SUBDOMINIO_PLATAFORMA

log = logging.getLogger(__name__)
USUARIO_SISTEMA = "sistema"
# Cambios de estado que puede hacer un operador sobre una empresa.
TRANSICIONES = {"suspender": ("activo", "suspendido"), "reactivar": ("suspendido", "activo")}


async def registrar(accion: str, usuario: str, ip: str | None = None, **detalle) -> None:
    """Bitácora de auditoría de la plataforma (base MongoDB de plataforma)."""
    # Sin IP explícita, el evento toma la de la petición en curso.
    extra = {"ip": ip} if ip else {}
    await auditoria.registrar(
        obtener_configuracion().mongodb_base_plataforma,
        EventoAuditoria(
            accion=accion, usuario=usuario, tenant_id=TENANT_PLATAFORMA, detalle=detalle, **extra
        ),
    )


def _email(valor: str) -> str:
    email = (valor or "").strip().lower()
    if "@" not in email or len(email) > 254 or email.startswith("@") or email.endswith("@"):
        raise ErrorDominio("USR_VAL_002", "Email inválido.")
    return email


# --- Empresas -----------------------------------------------------------------------


async def listar_empresas() -> list[dict]:
    return await run_in_threadpool(tenants.listar_resumen)


async def cambiar_estado_empresa(actor: Sesion, tenant_id: str, accion: str, ip: str | None):
    desde, hacia = TRANSICIONES[accion]
    tenant = await run_in_threadpool(tenants.obtener_por_id, tenant_id)
    if tenant is None:
        raise ErrorDominio("TEN_VAL_000", tenant_id)
    if tenant.estado != desde:
        raise ErrorDominio("PLA_VAL_003", f"{tenant.slug}: {tenant.estado} → {hacia}")
    await run_in_threadpool(tenants.actualizar_estado, tenant.id, hacia)
    # Esta réplica lo ve al instante; las demás, al vencer su caché (60 s).
    resolucion.limpiar_cache()
    await registrar(f"plataforma.empresa_{accion}", actor.email, ip, slug=tenant.slug)


# --- Invitaciones -------------------------------------------------------------------


def enlace_registro(email: str) -> str:
    """Link fijo del registro con el email ya cargado (el email no es un secreto)."""
    return f"{correos.direccion_registro()}?{urlencode({'email': email})}"


async def listar_invitaciones() -> list[Invitacion]:
    return await run_in_threadpool(repositorios.listar_invitaciones)


async def invitar(
    actor: Sesion, email: str, enviar: bool, ip: str | None
) -> tuple[Invitacion, str, str | None]:
    """Crea la invitación (vence a las 24 h) y, si se pide, la envía por correo.

    Devuelve la invitación, el link y el resultado del envío (None si solo se copió).
    """
    email = _email(email)
    invitacion = await run_in_threadpool(
        repositorios.crear_invitacion, email, actor.email, ahora_utc() + VIGENCIA_INVITACION
    )
    enlace = enlace_registro(email)
    envio = await correos.invitacion(email, enlace, actor.nombre) if enviar else None
    await registrar(
        "plataforma.invitacion_creada",
        actor.email,
        ip,
        invitacion=invitacion.id,
        email=email,
        envio=envio or "copiada",
    )
    return invitacion, enlace, envio


async def revocar(actor: Sesion, invitacion_id: str, ip: str | None) -> None:
    if not await run_in_threadpool(repositorios.revocar_invitacion, invitacion_id):
        raise ErrorDominio("PLA_VAL_004", invitacion_id)
    await registrar("plataforma.invitacion_revocada", actor.email, ip, invitacion=invitacion_id)


# --- Operadores ---------------------------------------------------------------------


async def listar_operadores() -> list[Operador]:
    return await run_in_threadpool(repositorios.listar_operadores)


async def alta_operador(
    actor: Sesion, email: str, nombre: str | None, ip: str | None
) -> tuple[Operador, str]:
    email = _email(email)
    operador = await run_in_threadpool(
        repositorios.crear_operador, email, (nombre or "").strip() or None, actor.email
    )
    if operador is None:
        raise ErrorDominio("PLA_VAL_001", email)
    aviso = await correos.operador(email, actor.nombre)
    await registrar("plataforma.operador_alta", actor.email, ip, operador=email, aviso=aviso)
    return operador, aviso


async def cambiar_idioma(actor: Sesion, idioma: str) -> None:
    await run_in_threadpool(repositorios.guardar_idioma_operador, actor.usuario_id, idioma)


async def baja_operador(actor: Sesion, operador_id: str, ip: str | None) -> None:
    if operador_id == actor.usuario_id:
        raise ErrorDominio("PLA_VAL_002", "No podés darte de baja a vos mismo.")
    operador = await run_in_threadpool(repositorios.obtener_operador, operador_id)
    if operador is None or operador.estado == "baja":
        raise ErrorDominio("PLA_VAL_000", operador_id)
    await run_in_threadpool(repositorios.dar_de_baja_operador, operador_id)
    organizacion = await run_in_threadpool(repositorios.organizacion)
    if operador.auth0_usuario_id and organizacion and obtener_configuracion().auth0_configurado:
        # Sin membresía en la organización, Auth0 ni siquiera le emite un token.
        try:
            await ClienteAuth0().quitar_de_organizacion(organizacion, operador.auth0_usuario_id)
        except ErrorDominio:
            log.exception("No se pudo quitar de Auth0 al operador %s.", operador.email)
    await registrar("plataforma.operador_baja", actor.email, ip, operador=operador.email)


# --- Puesta en marcha (comando) -------------------------------------------------------


async def crear_operador_inicial(email: str, nombre: str | None) -> tuple[Operador, str, str]:
    """Prepara la consola (organización de Auth0 y direcciones) y da de alta un operador.

    Es idempotente: se puede correr de nuevo para sumar operadores o reparar Auth0.
    """
    email = _email(email)
    await run_in_threadpool(tenants_servicios.migrar_catalogo)
    cfg = obtener_configuracion()
    organizacion = await run_in_threadpool(repositorios.organizacion)
    if cfg.auth0_configurado:
        auth0 = ClienteAuth0(cfg)
        await auth0.registrar_direcciones(correos.direccion_plataforma())
        if not organizacion:
            organizacion = await auth0.crear_organizacion(
                SUBDOMINIO_PLATAFORMA, f"{cfg.app_nombre} · Plataforma"
            )
            await run_in_threadpool(repositorios.guardar_organizacion, organizacion)
    operador = await run_in_threadpool(
        repositorios.crear_operador, email, (nombre or "").strip() or None, USUARIO_SISTEMA
    )
    if operador is None:
        operador = await run_in_threadpool(repositorios.obtener_operador_por_email, email)
        aviso = "ya existía"
    else:
        aviso = await correos.operador(email, cfg.app_nombre)
        await registrar("plataforma.operador_alta", USUARIO_SISTEMA, operador=email, aviso=aviso)
    return operador, aviso, organizacion or ""
