"""Rutas que devuelven páginas HTML."""

import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad.dependencias import sesion_actual, tenant_actual
from app.identidad.entidades import ACCIONES, AMBIENTES, MODULO_SOPORTE_OPERADOR, ROLES
from app.modulos.monitor.entidades import METRICAS
from app.modulos.soporte.entidades import TABLAS_CON_HISTORIA
from app.plataforma import correos as correos_plataforma
from app.plataforma.ingreso import sesion_operador
from app.tenants import resolucion
from app.tenants.entidades import PATRON_SLUG, Tenant
from app.web.i18n import IDIOMAS, LOCALES, idioma_actual, traducir
from app.web.menu import ICONOS, MENU

DIRECTORIO_WEB = Path(__file__).parent
DIRECTORIO_ESTATICOS = DIRECTORIO_WEB / "static"


def _version_estaticos() -> str:
    """Huella de los estáticos y plantillas (de estas sale el CSS compilado).

    NGINX los entrega con caché larga; agregarla a cada URL obliga al navegador a
    pedir la versión nueva apenas cambia algún archivo.
    """
    huella = hashlib.sha256()
    for archivo in sorted(DIRECTORIO_WEB.rglob("*")):
        if archivo.is_file() and archivo.suffix != ".pyc":
            huella.update(archivo.relative_to(DIRECTORIO_WEB).as_posix().encode())
            huella.update(archivo.read_bytes())
    return huella.hexdigest()[:12]


plantillas = Jinja2Templates(directory=DIRECTORIO_WEB / "templates")
plantillas.env.globals.update(
    t=traducir,
    menu=MENU,
    iconos=ICONOS,
    version_estaticos=_version_estaticos(),
    idioma_actual=idioma_actual,
    idiomas=IDIOMAS,
    locales=LOCALES,
)

router = APIRouter(include_in_schema=False)


def _subdominio_de_plataforma(request: Request) -> str | None:
    """ "plataforma" o "registro" si la petición llega por esos subdominios."""
    host, dominio = request.headers.get("host", ""), obtener_configuracion().dominio_base
    if resolucion.es_plataforma(host, dominio):
        return "plataforma"
    if resolucion.es_registro(host, dominio):
        return "registro"
    return None


@router.get("/", response_class=HTMLResponse)
async def inicio(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    subdominio = _subdominio_de_plataforma(request)
    if subdominio == "plataforma":
        sesion = await sesion_operador(request)
        if sesion is None:
            return RedirectResponse("/ingresar", status_code=303)
        return plantillas.TemplateResponse(
            request,
            "paginas/plataforma.html",
            {"sesion": sesion, "url_registro": correos_plataforma.direccion_registro()},
        )
    if subdominio == "registro":
        return RedirectResponse(correos_plataforma.RUTA_REGISTRO, status_code=303)
    if tenant is None:
        return _presentacion(request)
    sesion = await sesion_actual(request, tenant)
    if sesion is None:
        return RedirectResponse("/ingresar", status_code=303)
    if sesion.es_operador:
        # La sesión de soporte de un operador solo tiene la vista acotada de Soporte.
        return RedirectResponse("/soporte", status_code=303)
    return plantillas.TemplateResponse(
        request,
        "paginas/inicio.html",
        {"activo": "inicio", "sesion": sesion, "tenant": tenant, "ambientes": AMBIENTES},
    )


def _presentacion(request: Request, direccion: str = "", error: str | None = None, estado=200):
    """Página pública del dominio principal (CU-000-011-A)."""
    return plantillas.TemplateResponse(
        request,
        "paginas/presentacion.html",
        {
            "dominio": obtener_configuracion().dominio_base,
            "url_registro": correos_plataforma.direccion_registro(),
            "direccion": direccion,
            "error": error,
        },
        status_code=estado,
    )


def _slug_de_direccion(texto: str, dominio: str) -> str | None:
    """Acepta "acme", "acme.<dominio>" o la dirección completa (https://acme.<dominio>/...)."""
    valor = re.sub(r"^[a-z]+://", "", (texto or "").strip().lower())
    valor = valor.split("/", 1)[0].split(":", 1)[0].rstrip(".")
    sufijo = f".{dominio.lower()}"
    if valor.endswith(sufijo):
        valor = valor[: -len(sufijo)]
    if not PATRON_SLUG.match(valor) or resolucion.es_reservado(valor):
        return None
    return valor


@router.get("/ingresar-empresa", response_class=HTMLResponse)
async def ingresar_empresa(
    request: Request, direccion: str = "", tenant: Tenant | None = Depends(tenant_actual)
):
    """Desde la presentación, lleva al ingreso de la empresa indicada (CU-000-011-A)."""
    if tenant is not None or _subdominio_de_plataforma(request):
        return RedirectResponse("/", status_code=303)
    slug = _slug_de_direccion(direccion, obtener_configuracion().dominio_base)
    empresa = await resolucion.obtener_tenant(slug) if slug else None
    if empresa is not None and empresa.estado == "activo":
        return RedirectResponse(
            f"{correos_plataforma.direccion_empresa(slug)}/ingresar", status_code=303
        )
    # Una dirección inexistente o una empresa suspendida reciben la misma respuesta.
    return _presentacion(
        request, direccion.strip()[:120], traducir("presentacion.form.no_existe"), estado=404
    )


@router.get("/ingresar", response_class=HTMLResponse)
async def ingresar(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    if _subdominio_de_plataforma(request) == "plataforma":
        if await sesion_operador(request):
            return RedirectResponse("/", status_code=303)
        return plantillas.TemplateResponse(
            request, "paginas/ingresar.html", {"subtitulo": traducir("plataforma.consola")}
        )
    if tenant is None:
        return RedirectResponse("/", status_code=303)
    if await sesion_actual(request, tenant):
        return RedirectResponse("/", status_code=303)
    return plantillas.TemplateResponse(request, "paginas/ingresar.html", {"tenant": tenant})


@router.get(correos_plataforma.RUTA_REGISTRO, response_class=HTMLResponse)
async def registrar_empresa(request: Request, email: str = ""):
    """Registro de una empresa invitada (solo en ``registrar-empresa.<dominio_base>``)."""
    if _subdominio_de_plataforma(request) != "registro":
        raise ErrorDominio("PLA_INV_000")
    return plantillas.TemplateResponse(
        request,
        "paginas/registro_empresa.html",
        {
            "email": email.strip().lower()[:254],
            "dominio_base": obtener_configuracion().dominio_base,
        },
    )


async def _pagina_de_modulo(
    request: Request,
    tenant: Tenant | None,
    modulo: str | tuple[str, ...],
    plantilla: str,
    contexto: dict,
):
    """Renderiza una página que exige sesión y acceso del rol al módulo (o a alguno)."""
    if tenant is None:
        return RedirectResponse("/", status_code=303)
    sesion = await sesion_actual(request, tenant)
    if sesion is None:
        return RedirectResponse("/ingresar", status_code=303)
    modulos = (modulo,) if isinstance(modulo, str) else modulo
    if not any(sesion.puede_ver(m) for m in modulos):
        raise ErrorDominio("USR_PER_000", ", ".join(modulos))
    return plantillas.TemplateResponse(
        request,
        plantilla,
        {"activo": modulos[0], "sesion": sesion, "tenant": tenant, **contexto},
    )


@router.get("/conexiones", response_class=HTMLResponse)
async def conexiones(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(
        request, tenant, "conexiones", "paginas/conexiones.html", {"ambientes": AMBIENTES}
    )


@router.get("/monitor", response_class=HTMLResponse)
async def monitor(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(request, tenant, "monitor", "paginas/monitor.html", {})


@router.get("/alertas", response_class=HTMLResponse)
async def alertas(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(
        request,
        tenant,
        "alertas",
        "paginas/alertas.html",
        {
            "roles": ROLES,
            "ambientes": AMBIENTES,
            "metricas": list(METRICAS),
            "textos_roles": {rol: traducir(f"rol.{rol}") for rol in ROLES},
        },
    )


@router.get("/workarounds", response_class=HTMLResponse)
async def workarounds(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(
        request,
        tenant,
        "workarounds",
        "paginas/workarounds.html",
        {
            "roles": ROLES,
            "ambientes": AMBIENTES,
            "textos_roles": {r: traducir(f"rol.{r}") for r in ROLES},
        },
    )


@router.get("/sandbox", response_class=HTMLResponse)
async def sandbox(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(request, tenant, "sandbox", "paginas/sandbox.html", {})


@router.get("/chat", response_class=HTMLResponse)
async def chat(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(request, tenant, "chat", "paginas/chat.html", {})


@router.get("/contexto", response_class=HTMLResponse)
async def contexto(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(
        request,
        tenant,
        "contexto",
        "paginas/contexto.html",
        {
            "roles": ROLES,
            "ambientes": AMBIENTES,
            "textos_roles": {r: traducir(f"rol.{r}") for r in ROLES},
        },
    )


@router.get("/auditoria", response_class=HTMLResponse)
async def auditoria(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(
        request, tenant, "auditoria", "paginas/auditoria.html", {"ambientes": AMBIENTES}
    )


@router.get("/schema", response_class=HTMLResponse)
async def schema(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(request, tenant, "schema", "paginas/schema.html", {})


@router.get("/buenas-practicas", response_class=HTMLResponse)
async def buenas_practicas(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(
        request, tenant, "buenas_practicas", "paginas/buenas_practicas.html", {}
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
            "acciones": ACCIONES,
            "textos_roles": {rol: traducir(f"rol.{rol}") for rol in ROLES},
            "textos_estados": {
                e: traducir(f"estado.{e}") for e in ("invitado", "activo", "baja", "inactivo")
            },
        },
    )


@router.get("/soporte", response_class=HTMLResponse)
async def soporte(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    return await _pagina_de_modulo(
        request,
        tenant,
        ("soporte", MODULO_SOPORTE_OPERADOR),
        "paginas/soporte.html",
        {
            "textos_servicios": {
                e: traducir(f"servicios.estado.{e}") for e in ("online", "error", "warning")
            },
            "tablas_con_historia": TABLAS_CON_HISTORIA,
        },
    )


@router.get("/perfil", response_class=HTMLResponse)
async def perfil(request: Request, tenant: Tenant | None = Depends(tenant_actual)):
    # Todos los roles tienen su perfil (todos ven el Inicio).
    return await _pagina_de_modulo(
        request, tenant, "inicio", "paginas/perfil.html", {"activo": "perfil"}
    )


# Manifest de la PWA en el idioma del usuario (descripción y lang).
MANIFEST = json.loads((DIRECTORIO_ESTATICOS / "manifest.webmanifest").read_text(encoding="utf-8"))


@router.get("/manifest.webmanifest")
async def manifest() -> JSONResponse:
    return JSONResponse(
        {**MANIFEST, "description": traducir("app.descripcion"), "lang": idioma_actual()},
        media_type="application/manifest+json",
    )


@router.get("/sw.js")
async def service_worker() -> FileResponse:
    """El service worker se sirve desde la raíz para que su alcance cubra toda la app."""
    return FileResponse(DIRECTORIO_ESTATICOS / "sw.js", media_type="text/javascript")
