"""Resolución del tenant y de la sesión de cada petición."""

from fastapi import Request

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import sesiones
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
    return sesion if sesion and sesion.tenant_id == str(tenant.id) else None


def url_retorno(request: Request, ruta: str) -> str:
    return f"{request.url.scheme}://{request.headers.get('host', '')}{ruta}"


def ip_cliente(request: Request) -> str | None:
    return request.client.host if request.client else None
