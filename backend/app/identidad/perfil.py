"""Mi perfil: datos propios, contraseña y dispositivos con notificaciones (CU-001-001-C).

Cada usuario ve y cambia solo lo suyo. El rol y los ambientes los define el
Administrador: acá se muestran en solo lectura.
"""

import re

from starlette.concurrency import run_in_threadpool

from app.core.errores import ErrorDominio
from app.identidad import repositorios, usuarios
from app.identidad.auth0 import ClienteAuth0
from app.identidad.entidades import Sesion
from app.modulos.web_push import repositorios as dispositivos
from app.tenants.entidades import Tenant

LARGO_MAXIMO_NOMBRE = 150
# Auth0 antepone la conexión al id del usuario: "google-oauth2|…", "auth0|…".
METODOS = {"google-oauth2": "google", "auth0": "contrasena"}
NAVEGADORES = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
SISTEMAS = (
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("iPad", "iOS"),
    ("Windows", "Windows"),
    ("Mac OS", "macOS"),
    ("Linux", "Linux"),
)


def metodo_de_ingreso(auth0_usuario_id: str | None) -> str | None:
    if not auth0_usuario_id:
        return None
    return METODOS.get(auth0_usuario_id.split("|", 1)[0], "otro")


def describir_agente(agente: str | None) -> str:
    """ "Chrome · Android" a partir del User-Agent con que se suscribió el dispositivo."""
    texto = agente or ""
    navegador = next((n for marca, n in NAVEGADORES if marca in texto), None)
    sistema = next((s for marca, s in SISTEMAS if re.search(marca, texto)), None)
    return " · ".join(p for p in (navegador, sistema) if p) or "—"


async def datos(tenant: Tenant, sesion: Sesion) -> dict:
    usuario = await usuarios.obtener_propio(tenant, sesion)
    lista = await run_in_threadpool(
        dispositivos.listar_del_usuario, tenant.base_sqlserver, str(tenant.id), usuario.id
    )
    return {
        "email": usuario.email,
        "nombre": usuario.nombre,
        "rol": usuario.rol,
        "ambientes": list(usuario.ambientes),
        "ultimo_ingreso": usuario.ultimo_ingreso.isoformat() + "Z"
        if usuario.ultimo_ingreso
        else None,
        "idioma": usuario.idioma,
        "metodo": metodo_de_ingreso(usuario.auth0_usuario_id),
        "dispositivos": [
            {
                "id": d["id"],
                "descripcion": describir_agente(d["agente"]),
                "creada_en": d["creada_en"].isoformat() + "Z" if d["creada_en"] else None,
            }
            for d in lista
        ],
    }


async def cambiar_nombre(tenant: Tenant, sesion: Sesion, nombre: str | None) -> None:
    nombre = (nombre or "").strip() or None
    if nombre and len(nombre) > LARGO_MAXIMO_NOMBRE:
        raise ErrorDominio("USR_VAL_002", "Nombre demasiado largo.")
    await run_in_threadpool(
        repositorios.guardar_nombre,
        tenant.base_sqlserver,
        str(tenant.id),
        sesion.usuario_id,
        nombre,
    )
    await usuarios.auditar_propio(tenant, sesion, "usuario.perfil", {"nombre": nombre})


async def cambiar_contrasena(tenant: Tenant, sesion: Sesion) -> None:
    """Auth0 envía el correo con el enlace para definir la contraseña nueva."""
    usuario = await usuarios.obtener_propio(tenant, sesion)
    if metodo_de_ingreso(usuario.auth0_usuario_id) != "contrasena":
        raise ErrorDominio("USR_VAL_008")
    await ClienteAuth0().cambiar_contrasena(usuario.email, tenant.auth0_organizacion_id)
    await usuarios.auditar_propio(tenant, sesion, "usuario.cambio_contrasena", {})


async def quitar_dispositivo(tenant: Tenant, sesion: Sesion, dispositivo_id: str) -> None:
    if not await run_in_threadpool(
        dispositivos.eliminar_del_usuario,
        tenant.base_sqlserver,
        str(tenant.id),
        sesion.usuario_id,
        dispositivo_id,
    ):
        raise ErrorDominio("USR_VAL_002", "Dispositivo inexistente.")
    await usuarios.auditar_propio(
        tenant, sesion, "usuario.dispositivo_quitado", {"id": dispositivo_id}
    )
