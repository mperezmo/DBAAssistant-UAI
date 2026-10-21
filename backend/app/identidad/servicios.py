"""Casos de uso de sesión: Log in con MFA (CU-001-001-A) y Log off (CU-001-001-B)."""

import secrets
import time
from dataclasses import dataclass
from functools import lru_cache

from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import reautenticacion, repositorios, sesiones
from app.identidad.auth0 import ClienteAuth0
from app.identidad.entidades import Sesion
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.tenants.entidades import Tenant
from app.web.i18n import idioma_actual


@lru_cache
def cliente_auth0() -> ClienteAuth0:
    return ClienteAuth0()


def _verificar_tenant_habilitado(tenant: Tenant) -> None:
    if tenant.estado != "activo" or not tenant.auth0_organizacion_id:
        raise ErrorDominio("TEN_VAL_003", tenant.slug)


@dataclass(frozen=True)
class ResultadoIngreso:
    sesion: Sesion
    volver: str
    reautenticacion: bool


async def iniciar_ingreso(
    tenant: Tenant,
    redirect_uri: str,
    reautenticar: Sesion | None = None,
    volver: str = "/",
) -> str:
    """Prepara el ingreso y devuelve la URL de Auth0 a la que se redirige al usuario.

    Con ``reautenticar`` no se abre una sesión nueva: el usuario confirma su identidad
    con el segundo factor y vuelve a ``volver``.
    """
    _verificar_tenant_habilitado(tenant)
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    await sesiones.guardar_ingreso(
        state,
        {
            "nonce": nonce,
            "tenant": tenant.slug,
            "sesion_id": reautenticar.id if reautenticar else None,
            "volver": reautenticacion.ruta_de_retorno_valida(volver),
        },
    )
    return cliente_auth0().url_autorizacion(
        redirect_uri,
        state,
        nonce,
        tenant.auth0_organizacion_id,
        reautenticar is not None,
        idioma_actual(),
    )


async def completar_ingreso(
    tenant: Tenant, codigo: str, state: str, redirect_uri: str, ip: str | None
) -> ResultadoIngreso:
    """Valida la respuesta de Auth0, identifica al usuario del tenant y abre la sesión."""
    _verificar_tenant_habilitado(tenant)
    cfg = obtener_configuracion()
    auth0 = cliente_auth0()

    ingreso = await sesiones.consumir_ingreso(state)
    if not ingreso or ingreso["tenant"] != tenant.slug:
        raise ErrorDominio("USR_AUT_004")

    tokens = await auth0.intercambiar_codigo(codigo, redirect_uri)
    identidad = await auth0.validar_token(tokens["id_token"], cfg.auth0_client_id)
    await auth0.validar_token(tokens["access_token"], cfg.auth0_audience)

    if identidad.get("nonce") != ingreso["nonce"]:
        raise ErrorDominio("USR_AUT_004", "nonce")
    if identidad.get("org_id") != tenant.auth0_organizacion_id:
        raise ErrorDominio("USR_AUT_000", "La identidad no pertenece a esta empresa.")
    if cfg.auth0_exigir_mfa and "mfa" not in identidad.get("amr", []):
        raise ErrorDominio("USR_AUT_001", "No se completó el segundo factor.")

    auth0_id, email = identidad["sub"], (identidad.get("email") or "").lower()
    if ingreso.get("sesion_id"):
        sesion = await _confirmar_segundo_factor(tenant, ingreso["sesion_id"], identidad, ip)
        return ResultadoIngreso(sesion, ingreso["volver"], reautenticacion=True)

    usuario = await run_in_threadpool(
        repositorios.buscar_para_ingreso, tenant.base_sqlserver, str(tenant.id), auth0_id, email
    )
    if usuario is None:
        raise ErrorDominio("USR_VAL_000", email)
    if not usuario.puede_ingresar:
        raise ErrorDominio("USR_VAL_005", email)
    if usuario.auth0_usuario_id is None:
        # El primer ingreso vincula por email: exigir que Auth0 lo haya verificado evita
        # que alguien registre una cuenta con el email de un usuario invitado.
        if not identidad.get("email_verified"):
            raise ErrorDominio("USR_AUT_003", email)
        await run_in_threadpool(
            repositorios.activar,
            tenant.base_sqlserver,
            usuario.id,
            auth0_id,
            identidad.get("name"),
        )

    await run_in_threadpool(repositorios.registrar_ingreso, tenant.base_sqlserver, usuario.id)
    if usuario.idioma is None:
        # Sin elección propia, sus avisos salen en el idioma del navegador con que ingresa.
        await run_in_threadpool(
            repositorios.guardar_idioma,
            tenant.base_sqlserver,
            str(tenant.id),
            usuario.id,
            idioma_actual(),
        )
    sesion = await sesiones.crear(
        {
            "usuario_id": usuario.id,
            "tenant_id": str(tenant.id),
            "tenant_slug": tenant.slug,
            "email": usuario.email,
            "nombre": usuario.nombre or identidad.get("name") or usuario.email,
            "rol": usuario.rol,
        },
        cfg.sesion_duracion_segundos,
        tokens={"access_token": tokens["access_token"], "id_token": tokens["id_token"]},
    )
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="sesion.iniciada", usuario=usuario.email, tenant_id=str(tenant.id), ip=ip
        ),
    )
    return ResultadoIngreso(sesion, ingreso.get("volver", "/"), reautenticacion=False)


# Margen para aceptar que la autenticación informada por Auth0 es la recién realizada.
MAXIMA_ANTIGUEDAD_AUTENTICACION_SEGUNDOS = 120


async def _confirmar_segundo_factor(
    tenant: Tenant, sesion_id: str, identidad: dict, ip: str | None
) -> Sesion:
    sesion = await sesiones.obtener(sesion_id)
    if sesion is None:
        raise ErrorDominio("USR_AUT_005")
    usuario = await run_in_threadpool(
        repositorios.obtener, tenant.base_sqlserver, str(tenant.id), sesion.usuario_id
    )
    # La reautenticación debe hacerla la misma persona de la sesión, y recién.
    if usuario is None or usuario.auth0_usuario_id != identidad["sub"]:
        raise ErrorDominio("USR_AUT_000", "La identidad no coincide con la sesión.")
    tolerancia = obtener_configuracion().auth0_tolerancia_reloj_segundos
    antiguedad = time.time() - identidad.get("auth_time", 0)
    if antiguedad > MAXIMA_ANTIGUEDAD_AUTENTICACION_SEGUNDOS + tolerancia:
        raise ErrorDominio("USR_AUT_001", "La autenticación no es reciente.")
    await sesiones.registrar_segundo_factor(sesion_id)
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="sesion.segundo_factor_confirmado",
            usuario=sesion.email,
            tenant_id=str(tenant.id),
            ip=ip,
        ),
    )
    return sesion


async def cerrar_sesion(tenant: Tenant, sesion: Sesion, ip: str | None) -> None:
    await sesiones.eliminar(sesion.id)
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="sesion.cerrada", usuario=sesion.email, tenant_id=sesion.tenant_id, ip=ip
        ),
    )
