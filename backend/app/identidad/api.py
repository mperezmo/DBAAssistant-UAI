"""Rutas de ingreso y cierre de sesión."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import servicios, sesiones
from app.identidad.dependencias import ip_cliente, sesion_actual, tenant_actual, url_retorno
from app.tenants.entidades import Tenant

router = APIRouter(prefix="/auth", include_in_schema=False)


def _exigir_tenant(tenant: Tenant | None) -> Tenant:
    if tenant is None:
        raise ErrorDominio("TEN_VAL_000", "Ingresar desde la dirección de la empresa.")
    return tenant


@router.get("/login")
async def login(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    url = await servicios.iniciar_ingreso(
        _exigir_tenant(tenant), url_retorno(request, "/auth/callback")
    )
    return RedirectResponse(url, status_code=303)


@router.get("/confirmar")
async def confirmar(
    request: Request, volver: str = "/", tenant: Tenant | None = Depends(tenant_actual)
):
    """Reautenticación con segundo factor para habilitar acciones sobre PROD."""
    tenant = _exigir_tenant(tenant)
    sesion = await sesion_actual(request, tenant)
    if sesion is None:
        return RedirectResponse("/ingresar", status_code=303)
    url = await servicios.iniciar_ingreso(
        tenant, url_retorno(request, "/auth/callback"), reautenticar=sesion, volver=volver
    )
    return RedirectResponse(url, status_code=303)


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    tenant: Tenant | None = Depends(tenant_actual),
):
    if error or not code or not state:
        raise ErrorDominio("USR_AUT_000", error_description or error)
    resultado = await servicios.completar_ingreso(
        _exigir_tenant(tenant),
        code,
        state,
        url_retorno(request, "/auth/callback"),
        ip_cliente(request),
    )
    respuesta = RedirectResponse(resultado.volver, status_code=303)
    if resultado.reautenticacion:
        return respuesta
    sesion = resultado.sesion
    respuesta.set_cookie(
        sesiones.COOKIE,
        sesion.id,
        max_age=obtener_configuracion().sesion_duracion_segundos,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return respuesta


@router.post("/salir")
async def salir(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    tenant = _exigir_tenant(tenant)
    sesion = await sesion_actual(request, tenant)
    if sesion:
        await servicios.cerrar_sesion(tenant, sesion, ip_cliente(request))
    url = servicios.cliente_auth0().url_cierre_sesion(url_retorno(request, "/"))
    respuesta = RedirectResponse(url, status_code=303)
    respuesta.delete_cookie(sesiones.COOKIE)
    return respuesta
