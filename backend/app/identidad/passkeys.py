"""Passkeys (WebAuthn): confirmación biométrica de las acciones sensibles.

El ingreso sigue siendo Auth0 con MFA. Una passkey reemplaza solo la reconfirmación con
segundo factor que piden PROD, los DELETE o las tablas sensibles: el celular o la
notebook la resuelven con huella, rostro o PIN, sin salir de la aplicación. Si el
dispositivo no tiene una, se usa la reautenticación de Auth0 como hasta ahora.

Registrar una passkey exige haber confirmado el segundo factor de Auth0 hace poco: es
una credencial que después vale como segundo factor.
"""

import logging
import uuid

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.errores import ErrorDominio
from app.datos import cache
from app.identidad import reautenticacion, sesiones, usuarios
from app.identidad import repositorios_passkeys as repositorio
from app.identidad.entidades import Sesion
from app.tenants.entidades import Tenant
from app.web.i18n import traducir

log = logging.getLogger(__name__)

VIGENCIA_DESAFIO_SEGUNDOS = 300
LARGO_MAXIMO_NOMBRE = 100
MAXIMO_POR_USUARIO = 10


def _origen(request: Request) -> tuple[str, str]:
    """(rp_id, origen) de la petición: la passkey queda atada al subdominio del tenant."""
    host = request.headers.get("host", "")
    return host.split(":", 1)[0].lower(), f"{request.url.scheme}://{host}"


def _clave_desafio(tipo: str, sesion: Sesion) -> str:
    return f"passkey:{tipo}:{sesion.id}"


async def _guardar_desafio(tipo: str, sesion: Sesion, desafio: bytes) -> None:
    await cache.cliente().set(
        _clave_desafio(tipo, sesion), bytes_to_base64url(desafio), ex=VIGENCIA_DESAFIO_SEGUNDOS
    )


async def _consumir_desafio(tipo: str, sesion: Sesion) -> bytes:
    """Cada desafío sirve una sola vez."""
    valor = await cache.cliente().getdel(_clave_desafio(tipo, sesion))
    if not valor:
        raise ErrorDominio("USR_AUT_007")
    return base64url_to_bytes(valor)


async def _propias(tenant: Tenant, sesion: Sesion) -> list[repositorio.Passkey]:
    return await run_in_threadpool(
        repositorio.listar, tenant.base_sqlserver, str(tenant.id), sesion.usuario_id
    )


async def listar(tenant: Tenant, sesion: Sesion) -> list[dict]:
    return [
        {
            "id": p.id,
            "nombre": p.nombre,
            "creada_en": p.creada_en.isoformat() + "Z",
            "ultimo_uso": p.ultimo_uso.isoformat() + "Z" if p.ultimo_uso else None,
        }
        for p in await _propias(tenant, sesion)
    ]


# --- Registro (desde Mi perfil) -----------------------------------------------------


async def opciones_de_registro(tenant: Tenant, sesion: Sesion, request: Request) -> str:
    reautenticacion.exigir_segundo_factor_reciente(sesion)
    propias = await _propias(tenant, sesion)
    if len(propias) >= MAXIMO_POR_USUARIO:
        raise ErrorDominio("USR_VAL_002", "Demasiadas passkeys.")
    rp_id, _ = _origen(request)
    opciones = generate_registration_options(
        rp_id=rp_id,
        rp_name=f"{traducir('app.nombre')} · {tenant.nombre}",
        user_id=uuid.UUID(sesion.usuario_id).bytes,
        user_name=sesion.email,
        user_display_name=sesion.nombre or sesion.email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credencial_id)) for p in propias
        ],
    )
    await _guardar_desafio("registro", sesion, opciones.challenge)
    return options_to_json(opciones)


async def registrar(
    tenant: Tenant, sesion: Sesion, request: Request, credencial: dict, nombre: str
) -> None:
    reautenticacion.exigir_segundo_factor_reciente(sesion)
    nombre = (nombre or "").strip()[:LARGO_MAXIMO_NOMBRE] or traducir("perfil.passkey_sin_nombre")
    desafio = await _consumir_desafio("registro", sesion)
    rp_id, origen = _origen(request)
    try:
        verificada = verify_registration_response(
            credential=credencial,
            expected_challenge=desafio,
            expected_rp_id=rp_id,
            expected_origin=origen,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as error:
        raise ErrorDominio("USR_AUT_007", str(error)) from error
    await run_in_threadpool(
        repositorio.crear,
        tenant.base_sqlserver,
        str(tenant.id),
        sesion.usuario_id,
        bytes_to_base64url(verificada.credential_id),
        verificada.credential_public_key,
        verificada.sign_count,
        nombre,
    )
    await usuarios.auditar_propio(tenant, sesion, "usuario.passkey_registrada", {"nombre": nombre})


async def eliminar(tenant: Tenant, sesion: Sesion, passkey_id: str) -> None:
    try:
        uuid.UUID(passkey_id)
    except ValueError:
        raise ErrorDominio("USR_VAL_002", "Passkey inexistente.") from None
    if not await run_in_threadpool(
        repositorio.eliminar, tenant.base_sqlserver, str(tenant.id), sesion.usuario_id, passkey_id
    ):
        raise ErrorDominio("USR_VAL_002", "Passkey inexistente.")
    await usuarios.auditar_propio(tenant, sesion, "usuario.passkey_eliminada", {"id": passkey_id})


# --- Confirmación (reemplaza la reautenticación de Auth0) ---------------------------


async def opciones_de_confirmacion(tenant: Tenant, sesion: Sesion, request: Request) -> str | None:
    """Desafío para las passkeys del usuario, o None si no tiene ninguna."""
    propias = await _propias(tenant, sesion)
    if not propias:
        return None
    rp_id, _ = _origen(request)
    opciones = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credencial_id)) for p in propias
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    await _guardar_desafio("confirmacion", sesion, opciones.challenge)
    return options_to_json(opciones)


async def confirmar(tenant: Tenant, sesion: Sesion, request: Request, credencial: dict) -> None:
    """Valida la firma biométrica y deja el segundo factor vigente, como Auth0."""
    desafio = await _consumir_desafio("confirmacion", sesion)
    propias = {p.credencial_id: p for p in await _propias(tenant, sesion)}
    passkey = propias.get(str(credencial.get("id", "")))
    if passkey is None:
        raise ErrorDominio("USR_AUT_007", "passkey ajena o eliminada")
    rp_id, origen = _origen(request)
    try:
        verificada = verify_authentication_response(
            credential=credencial,
            expected_challenge=desafio,
            expected_rp_id=rp_id,
            expected_origin=origen,
            credential_public_key=passkey.clave_publica,
            credential_current_sign_count=passkey.contador,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as error:
        raise ErrorDominio("USR_AUT_007", str(error)) from error
    await run_in_threadpool(
        repositorio.registrar_uso,
        tenant.base_sqlserver,
        str(tenant.id),
        passkey.id,
        verificada.new_sign_count,
    )
    await sesiones.registrar_segundo_factor(sesion.id)
    await usuarios.auditar_propio(
        tenant,
        sesion,
        "sesion.segundo_factor_confirmado",
        {"metodo": "passkey", "passkey": passkey.nombre},
    )
