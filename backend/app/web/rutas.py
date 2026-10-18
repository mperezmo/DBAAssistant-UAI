"""Rutas que devuelven páginas HTML."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.web.i18n import traducir
from app.web.menu import ICONOS, MENU

DIRECTORIO_WEB = Path(__file__).parent
DIRECTORIO_ESTATICOS = DIRECTORIO_WEB / "static"

plantillas = Jinja2Templates(directory=DIRECTORIO_WEB / "templates")
plantillas.env.globals.update(t=traducir, menu=MENU, iconos=ICONOS)

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
async def inicio(request: Request) -> HTMLResponse:
    return plantillas.TemplateResponse(request, "paginas/inicio.html", {"activo": "inicio"})


@router.get("/sw.js")
async def service_worker() -> FileResponse:
    """El service worker se sirve desde la raíz para que su alcance cubra toda la app."""
    return FileResponse(DIRECTORIO_ESTATICOS / "sw.js", media_type="text/javascript")
