"""Rutas que devuelven páginas HTML."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.identidad.dependencias import sesion_actual, tenant_actual
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


@router.get("/sw.js")
async def service_worker() -> FileResponse:
    """El service worker se sirve desde la raíz para que su alcance cubra toda la app."""
    return FileResponse(DIRECTORIO_ESTATICOS / "sw.js", media_type="text/javascript")
