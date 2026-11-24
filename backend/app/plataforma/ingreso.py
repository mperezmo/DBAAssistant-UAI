"""Ingreso de los operadores a la consola de la plataforma.

Mismo flujo que las empresas (Auth0, Authorization Code, segundo factor), contra la
organización interna de la plataforma. La sesión no pertenece a ninguna empresa.
"""

import secrets
import time

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.core import contexto
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import reautenticacion, sesiones
from app.identidad.auth0 import CLAIM_MFA_EXIGIDO
from app.identidad.dependencias import (
    CABECERA_SOLICITUD,
    METODOS_SEGUROS,
    sesion_actual,
    tenant_actual,
)
from app.identidad.entidades import Sesion
from app.identidad.servicios import MAXIMA_ANTIGUEDAD_AUTENTICACION_SEGUNDOS, cliente_auth0
from app.plataforma import repositorios
from app.plataforma.entidades import ROL_OPERADOR, TENANT_PLATAFORMA
from app.plataforma.servicios import registrar
from app.tenants import resolucion
from app.web.i18n import idioma_actual


async def _organizacion() -> str:
    organizacion = await run_in_threadpool(repositorios.organizacion)
    if not organizacion or not obtener_configuracion().auth0_configurado:
        raise ErrorDominio("PLA_CFG_000")
    return organizacion


async def iniciar(redirect_uri: str, reautenticar: Sesion | None = None, volver: str = "/") -> str:
    """URL de Auth0 para ingresar o, con ``reautenticar``, para confirmar con el segundo
    factor antes de una acción sensible (entrar a una empresa como soporte)."""
    organizacion = await _organizacion()
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    await sesiones.guardar_ingreso(
        state,
        {
            "nonce": nonce,
            "tenant": TENANT_PLATAFORMA,
            "sesion_id": reautenticar.id if reautenticar else None,
            "volver": reautenticacion.ruta_de_retorno_valida(volver),
        },
    )
    return cliente_auth0().url_autorizacion(
        redirect_uri,
        state,
        nonce,
        organizacion,
        reautenticar is not None,
        idioma_actual(),
    )


async def completar(codigo: str, state: str, redirect_uri: str) -> tuple[Sesion, str, bool]:
    """Devuelve la sesión, a dónde volver y si fue una confirmación con segundo factor."""
    organizacion = await _organizacion()
    cfg = obtener_configuracion()
    auth0 = cliente_auth0()

    ingreso = await sesiones.consumir_ingreso(state)
    if not ingreso or ingreso.get("tenant") != TENANT_PLATAFORMA:
        raise ErrorDominio("USR_AUT_004")
    tokens = await auth0.intercambiar_codigo(codigo, redirect_uri)
    identidad = await auth0.validar_token(tokens["id_token"], cfg.auth0_client_id)
    await auth0.validar_token(tokens["access_token"], cfg.auth0_audience)
    if identidad.get("nonce") != ingreso["nonce"]:
        raise ErrorDominio("USR_AUT_004", "nonce")
    if identidad.get("org_id") != organizacion:
        raise ErrorDominio("USR_AUT_000", "La identidad no pertenece a la plataforma.")
    if cfg.auth0_exigir_mfa and "mfa" not in identidad.get("amr", []):
        raise ErrorDominio("USR_AUT_001", "No se completó el segundo factor.")

    auth0_id, email = identidad["sub"], (identidad.get("email") or "").lower()
    if ingreso.get("sesion_id"):
        sesion = await _confirmar_segundo_factor(ingreso["sesion_id"], identidad)
        return sesion, ingreso.get("volver", "/"), True

    operador = await run_in_threadpool(repositorios.buscar_para_ingreso, auth0_id, email)
    if operador is None or not operador.puede_ingresar:
        await registrar("plataforma.ingreso_rechazado", email or auth0_id)
        raise ErrorDominio("PLA_PER_000", email)
    if operador.auth0_usuario_id is None:
        # Primer ingreso: vincula por email, que Auth0 tiene que haber verificado.
        if not identidad.get("email_verified"):
            raise ErrorDominio("USR_AUT_003", email)
        await run_in_threadpool(
            repositorios.activar_operador, operador.id, auth0_id, identidad.get("name")
        )
    await run_in_threadpool(repositorios.registrar_ingreso_operador, operador.id)
    sesion = await sesiones.crear(
        {
            "usuario_id": operador.id,
            "tenant_id": TENANT_PLATAFORMA,
            "tenant_slug": TENANT_PLATAFORMA,
            "email": operador.email,
            "nombre": operador.nombre or identidad.get("name") or operador.email,
            "rol": ROL_OPERADOR,
        },
        cfg.sesion_duracion_segundos,
        tokens={"access_token": tokens["access_token"], "id_token": tokens["id_token"]},
    )
    await registrar("plataforma.sesion_iniciada", operador.email)
    return sesion, "/", False


async def _confirmar_segundo_factor(sesion_id: str, identidad: dict) -> Sesion:
    """Mismas reglas que en las empresas: la misma persona, recién y con el código."""
    sesion = await sesiones.obtener(sesion_id)
    if sesion is None or sesion.tenant_id != TENANT_PLATAFORMA:
        raise ErrorDominio("USR_AUT_005")
    operador = await run_in_threadpool(repositorios.obtener_operador, sesion.usuario_id)
    if operador is None or operador.auth0_usuario_id != identidad["sub"]:
        raise ErrorDominio("USR_AUT_000", "La identidad no coincide con la sesión.")
    if identidad.get(CLAIM_MFA_EXIGIDO) is not True:
        raise ErrorDominio("USR_AUT_001", "Auth0 no exigió el código (revisar la Action).")
    tolerancia = obtener_configuracion().auth0_tolerancia_reloj_segundos
    if (
        time.time() - identidad.get("iat", 0)
        > MAXIMA_ANTIGUEDAD_AUTENTICACION_SEGUNDOS + tolerancia
    ):
        raise ErrorDominio("USR_AUT_001", "La confirmación no es reciente.")
    await sesiones.registrar_segundo_factor(sesion_id)
    await registrar("plataforma.segundo_factor_confirmado", sesion.email)
    return sesion


async def sesion_operador(request: Request) -> Sesion | None:
    """Sesión vigente de un operador con acceso, solo en el subdominio de la plataforma."""
    if not resolucion.es_plataforma(
        request.headers.get("host", ""), obtener_configuracion().dominio_base
    ):
        return None
    sesion = await sesiones.obtener(request.cookies.get(sesiones.COOKIE))
    if sesion is None or sesion.tenant_id != TENANT_PLATAFORMA or sesion.rol != ROL_OPERADOR:
        return None
    # Se relee en cada petición: una baja corta el acceso de inmediato.
    operador = await run_in_threadpool(repositorios.obtener_operador, sesion.usuario_id)
    if operador is None or not operador.puede_ingresar:
        return None
    contexto.usuario.set(operador.email)
    request.state.usuario = operador.email
    if operador.idioma:
        contexto.idioma.set(operador.idioma)
    return sesion


async def requerir_operador(request: Request) -> Sesion:
    """Dependencia de la API de la consola: operador con sesión y cabecera anti-CSRF."""
    sesion = await sesion_operador(request)
    if sesion is None:
        raise ErrorDominio("USR_AUT_005")
    if request.method not in METODOS_SEGUROS and not request.headers.get(CABECERA_SOLICITUD):
        raise ErrorDominio("USR_AUT_006")
    return sesion


async def requerir_operador_en_cualquier_host(request: Request) -> Sesion:
    """Operador en la consola o en una sesión de soporte dentro de una empresa."""
    sesion = await sesion_operador(request)
    if sesion is None:
        candidata = await sesion_actual(request, await tenant_actual(request))
        sesion = candidata if candidata and candidata.es_operador else None
    if sesion is None:
        raise ErrorDominio("USR_AUT_005")
    if request.method not in METODOS_SEGUROS and not request.headers.get(CABECERA_SOLICITUD):
        raise ErrorDominio("USR_AUT_006")
    return sesion


async def cerrar(sesion: Sesion) -> None:
    await sesiones.eliminar(sesion.id)
    await registrar("plataforma.sesion_cerrada", sesion.email)
