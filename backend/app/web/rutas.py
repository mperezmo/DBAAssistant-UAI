"""Rutas que devuelven páginas HTML."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.errores import ErrorDominio
from app.identidad.dependencias import sesion_actual, tenant_actual
from app.identidad.entidades import AMBIENTES, ROLES
from app.tenants.entidades import Tenant
from app.web.i18n import traducir
from app.web.menu import ICONOS, MENU

DIRECTORIO_WEB = Path(__file__).parent
DIRECTORIO_ESTATICOS = DIRECTORIO_WEB / "static"

plantillas = Jinja2Templates(directory=DIRECTORIO_WEB / "templates")
plantillas.env.globals.update(t=traducir, menu=MENU, iconos=ICONOS)

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
async def inicio(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    if tenant is None:
        return plantillas.TemplateResponse(request, "paginas/sin_empresa.html")
    sesion = await sesion_actual(request, tenant)
    if sesion is None:
        return RedirectResponse("/ingresar", status_code=303)
    return plantillas.TemplateResponse(
        request, "paginas/inicio.html", {"activo": "inicio", "sesion": sesion, "tenant": tenant}
    )


@router.get("/ingresar", response_class=HTMLResponse)
async def ingresar(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    if tenant is None:
        return RedirectResponse("/", status_code=303)
    if await sesion_actual(request, tenant):
        return RedirectResponse("/", status_code=303)
    return plantillas.TemplateResponse(request, "paginas/ingresar.html", {"tenant": tenant})


async def _pagina_de_modulo(
    request: Request, tenant: Tenant | None, modulo: str, plantilla: str, contexto: dict
):
    """Renderiza una página que exige sesión y acceso del rol al módulo."""
    if tenant is None:
        return RedirectResponse("/", status_code=303)
    sesion = await sesion_actual(request, tenant)
    if sesion is None:
        return RedirectResponse("/ingresar", status_code=303)
    if not sesion.puede_ver(modulo):
        raise ErrorDominio("USR_PER_000", modulo)
    return plantillas.TemplateResponse(
        request,
        plantilla,
        {"activo": modulo, "sesion": sesion, "tenant": tenant, **contexto},
    )


@router.get("/conexiones", response_class=HTMLResponse)
async def conexiones(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(
        request, tenant, "conexiones", "paginas/conexiones.html", {"ambientes": AMBIENTES}
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(
        request,
        tenant,
        "admin",
        "paginas/admin.html",
        {
            "roles": ROLES,
            "ambientes": AMBIENTES,
            "textos_roles": {rol: traducir(f"rol.{rol}") for rol in ROLES},
            "textos_servicios": {
                e: traducir(f"servicios.estado.{e}") for e in ("online", "error", "warning")
            },
            "textos_estados": {
                e: traducir(f"estado.{e}") for e in ("invitado", "activo", "baja", "inactivo")
            },
        },
    )


@router.get("/sw.js")
async def service_worker() -> FileResponse:
    """El service worker se sirve desde la raíz para que su alcance cubra toda la app."""
    return FileResponse(DIRECTORIO_ESTATICOS / "sw.js", media_type="text/javascript")
