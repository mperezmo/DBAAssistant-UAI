"""API de la consola de la plataforma (operadores) y del registro de empresas."""

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import sesiones
from app.identidad.dependencias import (
    CABECERA_SOLICITUD,
    METODOS_SEGUROS,
    ip_cliente,
    tenant_actual,
)
from app.identidad.entidades import Sesion
from app.plataforma import registro, servicios, soporte
from app.plataforma.entidades import Invitacion, Operador, ahora_utc
from app.plataforma.ingreso import requerir_operador, requerir_operador_en_cualquier_host
from app.tenants import resolucion
from app.web.i18n import COOKIE_IDIOMA

router = APIRouter(prefix="/api/plataforma", tags=["plataforma"])
router_registro = APIRouter(prefix="/api/registro", tags=["registro"])
router_soporte = APIRouter(prefix="/auth", include_in_schema=False)
operador = Depends(requerir_operador)


def _fecha(valor) -> str | None:
    return valor.isoformat() + "Z" if valor else None


def _invitacion(i: Invitacion) -> dict:
    return {
        "id": i.id,
        "email": i.email,
        "estado": i.estado_visible(ahora_utc()),
        "creada_por": i.creada_por,
        "creada_en": _fecha(i.creada_en),
        "vence_en": _fecha(i.vence_en),
        "usada_en": _fecha(i.usada_en),
        "tenant_id": i.tenant_id,
    }


def _operador(o: Operador, actual: Sesion) -> dict:
    return {
        "id": o.id,
        "email": o.email,
        "nombre": o.nombre,
        "estado": o.estado,
        "creado_por": o.creado_por,
        "creado_en": _fecha(o.creado_en),
        "ultimo_ingreso": _fecha(o.ultimo_ingreso),
        "soy_yo": o.id == actual.usuario_id,
    }


# --- Consola (operadores) -----------------------------------------------------------


class EleccionIdioma(BaseModel):
    idioma: Literal["es", "en"]


@router.put("/idioma", status_code=204)
async def cambiar_idioma(
    datos: EleccionIdioma,
    response: Response,
    actual: Sesion = Depends(requerir_operador_en_cualquier_host),
) -> None:
    """Idioma del operador; también queda en la cookie para su pantalla de ingreso."""
    await servicios.cambiar_idioma(actual, datos.idioma)
    response.set_cookie(
        COOKIE_IDIOMA, datos.idioma, max_age=365 * 24 * 3600, secure=True, samesite="lax"
    )


@router.get("/empresas")
async def empresas(actual: Sesion = operador) -> dict:
    filas = await servicios.listar_empresas()
    return {
        "empresas": [
            {
                "id": str(f["id"]),
                "slug": f["slug"],
                "nombre": f["nombre"],
                "estado": f["estado"],
                "detalle_error": f["detalle_error"],
                "email_administrador": f["email_administrador"],
                "creado_en": _fecha(f["creado_en"]),
                "url": servicios.correos.direccion_empresa(f["slug"]),
            }
            for f in filas
        ]
    }


@router.post("/empresas/{tenant_id}/soporte")
async def entrar_como_soporte(tenant_id: str, actual: Sesion = operador) -> dict:
    """Pase para entrar a la empresa como soporte (pide el segundo factor reciente)."""
    return {"url": await soporte.crear_pase(actual, tenant_id)}


@router.post("/empresas/{tenant_id}/{accion}", status_code=204)
async def cambiar_estado(
    tenant_id: str, accion: str, request: Request, actual: Sesion = operador
) -> None:
    if accion not in servicios.TRANSICIONES:
        raise ErrorDominio("PLA_VAL_003", accion)
    await servicios.cambiar_estado_empresa(actual, tenant_id, accion, ip_cliente(request))


@router.get("/invitaciones")
async def invitaciones(actual: Sesion = operador) -> dict:
    return {
        "invitaciones": [_invitacion(i) for i in await servicios.listar_invitaciones()],
        "vigencia_horas": int(servicios.VIGENCIA_INVITACION.total_seconds() // 3600),
    }


class NuevaInvitacion(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    # True: se envía por correo; False: el operador copia el link y lo comparte él.
    enviar: bool = True


@router.post("/invitaciones", status_code=201)
async def invitar(datos: NuevaInvitacion, request: Request, actual: Sesion = operador) -> dict:
    invitacion, enlace, envio = await servicios.invitar(
        actual, datos.email, datos.enviar, ip_cliente(request)
    )
    return {"invitacion": _invitacion(invitacion), "enlace": enlace, "envio": envio}


@router.post("/invitaciones/{invitacion_id}/revocar", status_code=204)
async def revocar(invitacion_id: str, request: Request, actual: Sesion = operador) -> None:
    await servicios.revocar(actual, invitacion_id, ip_cliente(request))


@router.get("/operadores")
async def operadores(actual: Sesion = operador) -> dict:
    return {"operadores": [_operador(o, actual) for o in await servicios.listar_operadores()]}


class NuevoOperador(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    nombre: str | None = Field(default=None, max_length=150)


@router.post("/operadores", status_code=201)
async def alta_operador(datos: NuevoOperador, request: Request, actual: Sesion = operador) -> dict:
    nuevo, aviso = await servicios.alta_operador(
        actual, datos.email, datos.nombre, ip_cliente(request)
    )
    return {"operador": _operador(nuevo, actual), "aviso": aviso}


@router.delete("/operadores/{operador_id}", status_code=204)
async def baja_operador(operador_id: str, request: Request, actual: Sesion = operador) -> None:
    await servicios.baja_operador(actual, operador_id, ip_cliente(request))


# --- Registro de empresas -----------------------------------------------------------


async def en_registro(request: Request) -> None:
    """Solo en ``registrar-empresa.<dominio_base>`` y, al modificar, con cabecera anti-CSRF."""
    if not resolucion.es_registro(
        request.headers.get("host", ""), obtener_configuracion().dominio_base
    ):
        raise ErrorDominio("PLA_INV_000")
    if request.method not in METODOS_SEGUROS and not request.headers.get(CABECERA_SOLICITUD):
        raise ErrorDominio("USR_AUT_006")


registro_host = Depends(en_registro)


class PedidoCodigo(BaseModel):
    email: str = Field(min_length=3, max_length=254)


@router_registro.post("/codigo", status_code=204, dependencies=[registro_host])
async def pedir_codigo(datos: PedidoCodigo) -> None:
    await registro.solicitar_codigo(datos.email)


class Verificacion(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    codigo: str = Field(min_length=1, max_length=12)


@router_registro.post("/verificar", dependencies=[registro_host])
async def verificar(datos: Verificacion, request: Request, response: Response) -> dict:
    token = await registro.verificar_codigo(datos.email, datos.codigo, ip_cliente(request))
    response.set_cookie(
        registro.COOKIE,
        token,
        max_age=registro.VIGENCIA_SESION_SEGUNDOS,
        httponly=True,
        secure=True,
        samesite="strict",
    )
    return await registro.estado(await registro.exigir_sesion(token))


@router_registro.get("/sesion", dependencies=[registro_host])
async def sesion(request: Request) -> dict:
    return await registro.estado(await registro.exigir_sesion(request.cookies.get(registro.COOKIE)))


@router_registro.get("/disponible", dependencies=[registro_host])
async def disponible(slug: str, request: Request) -> dict:
    await registro.exigir_sesion(request.cookies.get(registro.COOKIE))
    return {"slug": slug, "disponible": await registro.disponible(slug)}


class DatosEmpresa(BaseModel):
    empresa: str = Field(min_length=2, max_length=150)
    slug: str = Field(min_length=1, max_length=40)
    nombre: str = Field(min_length=1, max_length=150)


@router_registro.post("/configurar", status_code=202, dependencies=[registro_host])
async def configurar(datos: DatosEmpresa, request: Request) -> dict:
    actual = await registro.exigir_sesion(request.cookies.get(registro.COOKIE))
    await registro.configurar(actual, datos.empresa, datos.slug, datos.nombre)
    return await registro.estado(actual)


@router_registro.post("/salir", status_code=204, dependencies=[registro_host])
async def salir(response: Response) -> None:
    response.delete_cookie(registro.COOKIE)


# --- Sesión de soporte en el subdominio de una empresa ---------------------------------


@router_soporte.get("/soporte")
async def canjear_pase(pase: str, tenant=Depends(tenant_actual)) -> RedirectResponse:
    if tenant is None:
        raise ErrorDominio("TEN_VAL_000", "Ingresar desde la dirección de la empresa.")
    sesion = await soporte.canjear_pase(tenant, pase)
    respuesta = RedirectResponse("/soporte", status_code=303)
    respuesta.set_cookie(
        sesiones.COOKIE,
        sesion.id,
        max_age=soporte.VIGENCIA_SESION_SEGUNDOS,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return respuesta
