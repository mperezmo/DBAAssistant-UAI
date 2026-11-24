"""Resolución del tenant y de la sesión de cada petición."""

from dataclasses import replace

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.core import contexto
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import repositorios, sesiones
from app.identidad.entidades import Sesion
from app.plataforma import repositorios as repositorios_plataforma
from app.tenants import resolucion
from app.tenants.entidades import Tenant


async def tenant_actual(request: Request) -> Tenant | None:
    """Tenant del subdominio, o None si se accede por el dominio base."""
    slug = resolucion.slug_desde_host(
        request.headers.get("host", ""), obtener_configuracion().dominio_base
    )
    # Los subdominios de la plataforma (consola de operadores, registro) no son empresas.
    if slug is None or resolucion.es_reservado(slug):
        return None
    tenant = await resolucion.obtener_tenant(slug)
    if tenant is None:
        raise ErrorDominio("TEN_VAL_000", slug)
    return tenant


async def sesion_actual(request: Request, tenant: Tenant | None) -> Sesion | None:
    # Una empresa suspendida por un operador corta las sesiones abiertas.
    if tenant is None or tenant.estado != "activo":
        return None
    sesion = await sesiones.obtener(request.cookies.get(sesiones.COOKIE))
    # La cookie es propia de cada subdominio; se valida igual el tenant por defensa.
    if sesion is None or sesion.tenant_id != str(tenant.id):
        return None
    if sesion.es_operador:
        return await _sesion_de_soporte(request, sesion)
    # El rol, los ambientes, las restricciones y el estado se releen en cada petición: un
    # cambio del Administrador rige desde la acción siguiente y una baja corta el acceso.
    usuario = await run_in_threadpool(
        repositorios.obtener, tenant.base_sqlserver, sesion.tenant_id, sesion.usuario_id
    )
    if usuario is None or not usuario.puede_ingresar:
        return None
    contexto.usuario.set(usuario.email)
    # Para el registro de errores HTTP, que corre fuera del contexto de la petición.
    request.state.usuario = usuario.email
    if usuario.idioma:
        contexto.idioma.set(usuario.idioma)
    return replace(
        sesion,
        rol=usuario.rol,
        nombre=usuario.nombre or sesion.nombre,
        ambientes=usuario.ambientes,
        restricciones=usuario.restricciones,
    )


async def _sesion_de_soporte(request: Request, sesion: Sesion) -> Sesion | None:
    """Sesión de soporte de un operador: se valida contra la plataforma, no contra los
    usuarios de la empresa (el operador no es uno de ellos)."""
    operador = await run_in_threadpool(repositorios_plataforma.obtener_operador, sesion.usuario_id)
    if operador is None or not operador.puede_ingresar:
        return None
    contexto.usuario.set(operador.email)
    request.state.usuario = operador.email
    if operador.idioma:
        contexto.idioma.set(operador.idioma)
    return sesion


def url_retorno(request: Request, ruta: str) -> str:
    return f"{request.url.scheme}://{request.headers.get('host', '')}{ruta}"


def ip_cliente(request: Request) -> str | None:
    return request.client.host if request.client else None


METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}
CABECERA_SOLICITUD = "X-Solicitud"


def requerir_modulo(*modulos: str):
    """Dependencia para la API: exige sesión vigente y acceso del rol a alguno de los módulos.

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
        if not any(sesion.puede_ver(m) for m in modulos):
            raise ErrorDominio("USR_PER_000", ", ".join(modulos))
        return tenant, sesion

    return dependencia
