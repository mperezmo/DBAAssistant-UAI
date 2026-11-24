"""Gestión de usuarios del tenant (CU-001-000 y CU-001-002).

Reglas:
- El email es único por tenant; un usuario dado de baja conserva su email durante
  el período de gracia y puede rehabilitarse; vencido ese plazo se purga.
- Un Administrador no puede quitarse su rol ni darse de baja, y siempre debe quedar
  al menos un Administrador vigente.
- El cambio de rol no cierra sesiones: el rol se relee en cada petición.
- Permisos por ambiente (CU-001-002-C): el rol fija el techo de cada ambiente; el
  Administrador solo puede quitar acciones (se guardan las restricciones), nunca ampliar.
"""

import logging
import re
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import invitaciones, repositorios
from app.identidad.auth0 import ClienteAuth0
from app.identidad.entidades import (
    ACCIONES,
    AMBIENTES,
    ROLES,
    Sesion,
    Usuario,
    clave_restriccion,
    techo,
)
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.tenants.entidades import Tenant
from app.web.i18n import IDIOMAS

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


async def cambiar_idioma(tenant: Tenant, actor: Sesion, idioma: str) -> None:
    """Idioma de la interfaz del propio usuario; lo usan también sus avisos."""
    if idioma not in IDIOMAS:
        raise ErrorDominio("USR_VAL_002", "Idioma inválido.")
    await run_in_threadpool(
        repositorios.guardar_idioma, tenant.base_sqlserver, str(tenant.id), actor.usuario_id, idioma
    )
    await _auditar(tenant, actor, "usuario.idioma", {"idioma": idioma})


async def invitar(tenant: Tenant, actor: Sesion, usuario_id: str, al_crear: bool = False) -> str:
    """Envía el aviso de acceso. Al crear, informa el resultado; al reenviar, falla si no sale."""
    usuario = await _obtener(tenant, usuario_id)
    if usuario.estado != "invitado":
        raise ErrorDominio("USR_VAL_007")
    estado = await invitaciones.enviar(tenant, usuario, actor.nombre or actor.email)
    await _auditar(tenant, actor, "usuario.invitacion", {"email": usuario.email, "estado": estado})
    if not al_crear and estado == invitaciones.SIN_CORREO:
        raise ErrorDominio("NTF_EML_001")
    if not al_crear and estado == invitaciones.FALLIDA:
        raise ErrorDominio("NTF_EML_000", usuario.email)
    return estado


async def obtener_propio(tenant: Tenant, actor: Sesion) -> Usuario:
    return await _obtener(tenant, actor.usuario_id)


async def auditar_propio(tenant: Tenant, actor: Sesion, accion: str, detalle: dict) -> None:
    await _auditar(tenant, actor, accion, detalle)


# --- Permisos por ambiente (CU-001-002-C) -------------------------------------------


def matriz_de_permisos(usuario: Usuario) -> dict:
    """Ambientes habilitados (fila Acceso) y, por ambiente, el techo del rol y lo permitido."""
    restringidas = set(usuario.restricciones)
    return {
        "usuario_id": usuario.id,
        "rol": usuario.rol,
        "ambientes": list(usuario.ambientes),
        "acciones": list(ACCIONES),
        "techo": {a: [x for x in ACCIONES if x in techo(usuario.rol, a)] for a in AMBIENTES},
        "permitidas": {
            a: [
                x
                for x in ACCIONES
                if x in techo(usuario.rol, a) and clave_restriccion(a, x) not in restringidas
            ]
            for a in AMBIENTES
        },
    }


async def permisos_de(tenant: Tenant, actor: Sesion, usuario_id: str) -> dict:
    return matriz_de_permisos(await _obtener(tenant, usuario_id))


async def guardar_permisos(
    tenant: Tenant,
    actor: Sesion,
    usuario_id: str,
    ambientes: tuple[str, ...],
    permitidas: dict[str, list[str]],
) -> dict:
    usuario = await _obtener(tenant, usuario_id)
    if usuario.estado == "baja":
        raise ErrorDominio("USR_VAL_006")
    if any(a not in AMBIENTES for a in ambientes) or any(a not in AMBIENTES for a in permitidas):
        raise ErrorDominio("USR_VAL_002", "Ambiente inválido.")
    for ambiente, acciones in permitidas.items():
        for accion in acciones:
            if accion not in ACCIONES:
                raise ErrorDominio("USR_VAL_002", f"Acción inválida: {accion}")
            # No se puede ampliar más allá de lo que permite el rol (USR_PER_003).
            if accion not in techo(usuario.rol, ambiente):
                raise ErrorDominio("USR_PER_003", f"{accion} en {ambiente}")
    ambientes = tuple(a for a in AMBIENTES if a in ambientes)
    # En los ambientes habilitados se guarda lo que se quitó; en los demás se conservan
    # las restricciones anteriores, por si se vuelven a habilitar.
    anteriores = {tuple(r.split(":", 1)) for r in usuario.restricciones}
    restricciones = sorted(
        {(a, x) for a, x in anteriores if a not in ambientes}
        | {
            (a, x)
            for a in ambientes
            for x in techo(usuario.rol, a)
            if x not in permitidas.get(a, [])
        }
    )
    await run_in_threadpool(
        repositorios.guardar_permisos,
        tenant.base_sqlserver,
        str(tenant.id),
        usuario.id,
        ambientes,
        restricciones,
    )
    await _auditar(
        tenant,
        actor,
        "usuario.permisos",
        {
            "email": usuario.email,
            "antes": {
                "ambientes": list(usuario.ambientes),
                "restricciones": sorted(usuario.restricciones),
            },
            "despues": {
                "ambientes": list(ambientes),
                "restricciones": [clave_restriccion(a, x) for a, x in restricciones],
            },
        },
    )
    return matriz_de_permisos(await _obtener(tenant, usuario.id))
