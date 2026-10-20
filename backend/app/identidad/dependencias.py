"""Resolución del tenant y de la sesión de cada petición."""

from dataclasses import replace

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.core import contexto
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import repositorios, sesiones
from app.identidad.entidades import Sesion
from app.tenants import resolucion
from app.tenants.entidades import Tenant


async def tenant_actual(request: Request) -> Tenant | None:
    """Tenant del subdominio, o None si se accede por el dominio base."""
    slug = resolucion.slug_desde_host(
        request.headers.get("host", ""), obtener_configuracion().dominio_base
    )
    if slug is None:
        return None
    tenant = await resolucion.obtener_tenant(slug)
    if tenant is None:
        raise ErrorDominio("TEN_VAL_000", slug)
    return tenant


async def sesion_actual(request: Request, tenant: Tenant | None) -> Sesion | None:
    if tenant is None:
        return None
    sesion = await sesiones.obtener(request.cookies.get(sesiones.COOKIE))
    # La cookie es propia de cada subdominio; se valida igual el tenant por defensa.
    if sesion is None or sesion.tenant_id != str(tenant.id):
        return None
    # El rol y el estado se releen en cada petición: un cambio de rol se ve con solo
    # recargar la página y una baja corta el acceso de inmediato.
    usuario = await run_in_threadpool(
        repositorios.obtener, tenant.base_sqlserver, sesion.tenant_id, sesion.usuario_id
    )
    if usuario is None or not usuario.puede_ingresar:
        return None
    contexto.usuario.set(usuario.email)
    return replace(sesion, rol=usuario.rol, nombre=usuario.nombre or sesion.nombre)


def url_retorno(request: Request, ruta: str) -> str:
    return f"{request.url.scheme}://{request.headers.get('host', '')}{ruta}"


def ip_cliente(request: Request) -> str | None:
    return request.client.host if request.client else None


METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}
CABECERA_SOLICITUD = "X-Solicitud"


def requerir_modulo(modulo: str):
    """Dependencia para la API: exige sesión vigente y acceso del rol al módulo.

    Las peticiones que modifican datos deben traer la cabecera ``X-Solicitud``,
    que un formulario de otro sitio no puede enviar sin una verificación CORS previa.
    """

    async def dependencia(request: Request) -> tuple[Tenant, Sesion]:
        tenant = await tenant_actual(request)
        sesion = await sesion_actual(request, tenant)
        if tenant is None or sesion is None:
            raise ErrorDominio("USR_AUT_005")
        if request.method not in METODOS_SEGUROS and not request.headers.get(CABECERA_SOLICITUD):
            raise ErrorDominio("USR_AUT_006")
        if not sesion.puede_ver(modulo):
            raise ErrorDominio("USR_PER_000", modulo)
        return tenant, sesion

    return dependencia
