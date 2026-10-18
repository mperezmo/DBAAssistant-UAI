"""Gestión de usuarios del tenant (CU-001-000 y CU-001-002).

Reglas:
- El email es único por tenant; un usuario dado de baja conserva su email durante
  el período de gracia y puede rehabilitarse; vencido ese plazo se purga.
- Un Administrador no puede quitarse su rol ni darse de baja, y siempre debe quedar
  al menos un Administrador vigente.
- El cambio de rol no cierra sesiones: el rol se relee en cada petición.
"""

import logging
import re
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import repositorios
from app.identidad.auth0 import ClienteAuth0
from app.identidad.entidades import AMBIENTES, ROLES, Sesion, Usuario
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.tenants.entidades import Tenant

log = logging.getLogger(__name__)
PATRON_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class DatosUsuario:
    email: str
    nombre: str | None
    rol: str
    ambientes: tuple[str, ...]


def _validar(datos: DatosUsuario) -> DatosUsuario:
    email = datos.email.strip().lower()
    if not PATRON_EMAIL.match(email):
        raise ErrorDominio("USR_VAL_002", "Email inválido.")
    if datos.rol not in ROLES:
        raise ErrorDominio("USR_PER_002", datos.rol)
    ambientes = tuple(a for a in AMBIENTES if a in set(datos.ambientes))
    if len(ambientes) != len(set(datos.ambientes)):
        raise ErrorDominio("USR_VAL_002", "Ambiente inválido.")
    nombre = (datos.nombre or "").strip() or None
    return DatosUsuario(email, nombre, datos.rol, ambientes)


async def _auditar(tenant: Tenant, actor: Sesion, accion: str, detalle: dict) -> None:
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion=accion, usuario=actor.email, tenant_id=str(tenant.id), detalle=detalle
        ),
    )


async def _obtener(tenant: Tenant, usuario_id: str) -> Usuario:
    usuario = await run_in_threadpool(
        repositorios.obtener, tenant.base_sqlserver, str(tenant.id), usuario_id
    )
    if usuario is None:
        raise ErrorDominio("USR_VAL_000", usuario_id)
    return usuario


async def _verificar_que_queda_un_administrador(tenant: Tenant, usuario: Usuario) -> None:
    if usuario.rol != "ADMINISTRADOR" or not usuario.puede_ingresar:
        return
    vigentes = await run_in_threadpool(
        repositorios.contar_administradores_vigentes, tenant.base_sqlserver, str(tenant.id)
    )
    if vigentes <= 1:
        raise ErrorDominio("USR_PER_005")


async def listar(tenant: Tenant, actor: Sesion, bajas: bool = False) -> list[Usuario]:
    """Lista los usuarios; antes purga las bajas que superaron el período de gracia."""
    purgados = await run_in_threadpool(
        repositorios.purgar_bajas, tenant.base_sqlserver, str(tenant.id)
    )
    for email in purgados:
        await _auditar(tenant, actor, "usuario.purgado", {"email": email})
    return await run_in_threadpool(
        repositorios.listar, tenant.base_sqlserver, str(tenant.id), bajas
    )


async def crear(tenant: Tenant, actor: Sesion, datos: DatosUsuario) -> Usuario:
    datos = _validar(datos)
    existente = await run_in_threadpool(
        repositorios.obtener_por_email, tenant.base_sqlserver, str(tenant.id), datos.email
    )
    if existente:
        raise ErrorDominio("USR_VAL_006" if existente.estado == "baja" else "USR_VAL_001")
    usuario_id = await run_in_threadpool(
        repositorios.crear,
        tenant.base_sqlserver,
        str(tenant.id),
        datos.email,
        datos.nombre,
        datos.rol,
        datos.ambientes,
    )
    await _auditar(
        tenant,
        actor,
        "usuario.creado",
        {"email": datos.email, "rol": datos.rol, "ambientes": list(datos.ambientes)},
    )
    return await _obtener(tenant, usuario_id)


async def modificar(tenant: Tenant, actor: Sesion, usuario_id: str, datos: DatosUsuario) -> Usuario:
    usuario = await _obtener(tenant, usuario_id)
    if usuario.estado == "baja":
        raise ErrorDominio("USR_VAL_006")
    datos = _validar(DatosUsuario(usuario.email, datos.nombre, datos.rol, datos.ambientes))
    if datos.rol != usuario.rol:
        if usuario.id == actor.usuario_id:
            raise ErrorDominio("USR_PER_004")
        await _verificar_que_queda_un_administrador(tenant, usuario)

    await run_in_threadpool(
        repositorios.actualizar,
        tenant.base_sqlserver,
        str(tenant.id),
        usuario.id,
        datos.nombre,
        datos.rol,
        datos.ambientes,
    )
    await _auditar(
        tenant,
        actor,
        "usuario.modificado",
        {
            "email": usuario.email,
            "antes": {"rol": usuario.rol, "ambientes": list(usuario.ambientes)},
            "despues": {"rol": datos.rol, "ambientes": list(datos.ambientes)},
        },
    )
    return await _obtener(tenant, usuario.id)


async def dar_baja(tenant: Tenant, actor: Sesion, usuario_id: str) -> None:
    usuario = await _obtener(tenant, usuario_id)
    if usuario.id == actor.usuario_id:
        raise ErrorDominio("USR_PER_004")
    await _verificar_que_queda_un_administrador(tenant, usuario)
    await run_in_threadpool(
        repositorios.dar_baja, tenant.base_sqlserver, str(tenant.id), usuario.id
    )

    cfg = obtener_configuracion()
    if usuario.auth0_usuario_id and tenant.auth0_organizacion_id and cfg.auth0_configurado:
        try:
            await ClienteAuth0(cfg).quitar_de_organizacion(
                tenant.auth0_organizacion_id, usuario.auth0_usuario_id
            )
        except ErrorDominio as error:
            # La baja ya rige en la app (no puede ingresar); Auth0 se reintenta a mano.
            log.warning("No se pudo quitar %s de la organización: %s", usuario.email, error)
    await _auditar(tenant, actor, "usuario.baja", {"email": usuario.email})


async def rehabilitar(tenant: Tenant, actor: Sesion, usuario_id: str) -> Usuario:
    usuario = await _obtener(tenant, usuario_id)
    if usuario.estado != "baja":
        raise ErrorDominio("USR_VAL_002", "El usuario no está dado de baja.")
    await run_in_threadpool(
        repositorios.rehabilitar, tenant.base_sqlserver, str(tenant.id), usuario.id
    )
    await _auditar(tenant, actor, "usuario.rehabilitado", {"email": usuario.email})
    return await _obtener(tenant, usuario.id)
