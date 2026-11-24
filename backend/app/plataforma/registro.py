"""Registro de una empresa por invitación (``registrar-empresa.<dominio_base>``).

La persona invitada prueba que controla el email con un código que se le envía por
correo; con eso abre una sesión de registro corta, completa los datos y dispara el alta.
El ingreso a la empresa creada sigue siendo por Auth0, con segundo factor.

Protecciones del código: 6 dígitos al azar, solo se guarda su hash, vence a los 10
minutos, admite 5 intentos y cada email puede pedir uno por minuto y 5 por hora. Las
respuestas no revelan si un email tiene una invitación.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool

from app.core.errores import ErrorDominio
from app.datos import cache
from app.plataforma import correos, repositorios
from app.plataforma.entidades import (
    DIGITOS_CODIGO,
    INTENTOS_CODIGO,
    VIGENCIA_CODIGO,
    Invitacion,
    ahora_utc,
)
from app.plataforma.servicios import registrar
from app.tenants import repositorios as tenants
from app.tenants import servicios as tenants_servicios
from app.tenants.entidades import validar_slug_nuevo

log = logging.getLogger(__name__)

COOKIE = "dba_registro"
VIGENCIA_SESION_SEGUNDOS = 30 * 60
ESPERA_ENTRE_CODIGOS_SEGUNDOS = 60
CODIGOS_POR_HORA = 5
VERIFICACIONES_POR_HORA_IP = 30
PREFIJO = "registro:"

# Un alta corre una sola vez a la vez, en cualquier réplica: el bloqueo vive en Redis y
# vence solo si la réplica que la corría se cae.
BLOQUEO_ALTA_SEGUNDOS = 15 * 60
# Referencias a las tareas en curso de esta réplica, para que no se descarten.
_tareas: set[asyncio.Task] = set()


def _redis():
    return cache.cliente()


def hash_codigo(invitacion_id: str, codigo: str) -> str:
    # El id de la invitación hace de sal: el mismo código no da el mismo hash en otra.
    return hashlib.sha256(f"{invitacion_id}:{codigo}".encode()).hexdigest()


def _normalizar(email: str) -> str:
    return (email or "").strip().lower()


async def _invitacion_abierta(email: str) -> Invitacion | None:
    invitacion = await run_in_threadpool(repositorios.obtener_invitacion_abierta, email)
    return invitacion if invitacion and invitacion.abierta(ahora_utc()) else None


# --- Código de verificación ---------------------------------------------------------


async def solicitar_codigo(email: str) -> None:
    """Envía un código si el email tiene una invitación abierta; si no, no hace nada.

    Los límites se aplican por email exista o no la invitación, para no revelarla.
    """
    email = _normalizar(email)
    if "@" not in email:
        raise ErrorDominio("USR_VAL_002", "Email inválido.")
    redis = _redis()
    if not await redis.set(
        f"{PREFIJO}espera:{email}", 1, nx=True, ex=ESPERA_ENTRE_CODIGOS_SEGUNDOS
    ):
        raise ErrorDominio("PLA_INV_002")
    clave_hora = f"{PREFIJO}hora:{email}"
    pedidos = await redis.incr(clave_hora)
    if pedidos == 1:
        await redis.expire(clave_hora, 3600)
    if pedidos > CODIGOS_POR_HORA:
        raise ErrorDominio("PLA_INV_002")

    invitacion = await _invitacion_abierta(email)
    if invitacion is None:
        await registrar("plataforma.registro_codigo_sin_invitacion", email)
        return
    codigo = "".join(secrets.choice("0123456789") for _ in range(DIGITOS_CODIGO))
    await run_in_threadpool(
        repositorios.guardar_codigo,
        invitacion.id,
        hash_codigo(invitacion.id, codigo),
        ahora_utc() + VIGENCIA_CODIGO,
    )
    envio = await correos.codigo(email, codigo)
    await registrar(
        "plataforma.registro_codigo_enviado", email, invitacion=invitacion.id, envio=envio
    )


async def verificar_codigo(email: str, codigo: str, ip: str | None) -> str:
    """Valida el código y abre la sesión de registro; devuelve su identificador."""
    email = _normalizar(email)
    redis = _redis()
    clave_ip = f"{PREFIJO}ip:{ip or 'desconocida'}"
    intentos_ip = await redis.incr(clave_ip)
    if intentos_ip == 1:
        await redis.expire(clave_ip, 3600)
    if intentos_ip > VERIFICACIONES_POR_HORA_IP:
        raise ErrorDominio("PLA_INV_002")

    invitacion = await _invitacion_abierta(email)
    ahora = ahora_utc()
    valido = (
        invitacion is not None
        and invitacion.codigo_hash is not None
        and invitacion.codigo_vence_en is not None
        and invitacion.codigo_vence_en > ahora
        and invitacion.codigo_intentos < INTENTOS_CODIGO
        and hmac.compare_digest(
            invitacion.codigo_hash, hash_codigo(invitacion.id, (codigo or "").strip())
        )
    )
    if not valido:
        if invitacion is not None and invitacion.codigo_hash is not None:
            await run_in_threadpool(
                repositorios.registrar_intento_fallido, invitacion.id, INTENTOS_CODIGO
            )
        await registrar("plataforma.registro_codigo_incorrecto", email)
        raise ErrorDominio("PLA_INV_001")

    await run_in_threadpool(repositorios.consumir_codigo, invitacion.id)
    token = secrets.token_urlsafe(32)
    await redis.set(
        f"{PREFIJO}sesion:{token}",
        json.dumps({"invitacion_id": invitacion.id, "email": email}),
        ex=VIGENCIA_SESION_SEGUNDOS,
    )
    await registrar("plataforma.registro_verificado", email, invitacion=invitacion.id)
    return token


# --- Sesión de registro y alta ------------------------------------------------------


@dataclass(frozen=True)
class SesionRegistro:
    token: str
    invitacion: Invitacion


async def sesion(token: str | None) -> SesionRegistro | None:
    if not token:
        return None
    crudo = await _redis().get(f"{PREFIJO}sesion:{token}")
    if not crudo:
        return None
    datos = json.loads(crudo)
    invitacion = await run_in_threadpool(repositorios.obtener_invitacion, datos["invitacion_id"])
    if invitacion is None or invitacion.email != datos["email"]:
        return None
    return SesionRegistro(token, invitacion)


async def exigir_sesion(token: str | None) -> SesionRegistro:
    actual = await sesion(token)
    # Una invitación revocada corta también una sesión de registro ya abierta.
    if actual is None or actual.invitacion.estado in ("revocada",):
        raise ErrorDominio("PLA_INV_000")
    return actual


async def disponible(slug: str) -> bool:
    validar_slug_nuevo(slug)
    return await run_in_threadpool(tenants.obtener_por_slug, slug) is None


def _clave_bloqueo(invitacion_id: str) -> str:
    return f"{PREFIJO}alta:{invitacion_id}"


def _clave_progreso(invitacion_id: str) -> str:
    return f"{PREFIJO}progreso:{invitacion_id}"


async def _guardar_progreso(invitacion_id: str, **datos) -> None:
    await _redis().set(_clave_progreso(invitacion_id), json.dumps(datos), ex=24 * 3600)


async def progreso(invitacion_id: str) -> dict:
    crudo = await _redis().get(_clave_progreso(invitacion_id))
    return json.loads(crudo) if crudo else {}


async def configurar(actual: SesionRegistro, empresa: str, slug: str, nombre: str) -> None:
    """Valida los datos y dispara el alta en segundo plano; el avance se consulta aparte."""
    invitacion = actual.invitacion
    empresa, nombre, slug = (empresa or "").strip(), (nombre or "").strip(), (slug or "").strip()
    if not 2 <= len(empresa) <= 150 or not 1 <= len(nombre) <= 150:
        raise ErrorDominio("USR_VAL_002", "Empresa y nombre son obligatorios.")
    validar_slug_nuevo(slug)
    if invitacion.estado == "usada":
        raise ErrorDominio("PLA_INV_003")

    # El bloqueo va primero: dos pedidos simultáneos no pueden preparar la misma alta.
    bloqueo = _clave_bloqueo(invitacion.id)
    if not await _redis().set(bloqueo, 1, nx=True, ex=BLOQUEO_ALTA_SEGUNDOS):
        raise ErrorDominio("PLA_INV_003")
    try:
        await _preparar_alta(invitacion, slug)
    except BaseException:
        await _redis().delete(bloqueo)
        raise
    await _guardar_progreso(invitacion.id, estado="en_curso", paso=None, slug=slug)
    await registrar(
        "plataforma.registro_iniciado", invitacion.email, invitacion=invitacion.id, slug=slug
    )
    tarea = asyncio.create_task(_dar_de_alta(invitacion, empresa, slug, nombre))
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)


async def _preparar_alta(invitacion: Invitacion, slug: str) -> None:
    existente = await run_in_threadpool(tenants.obtener_por_slug, slug)
    if invitacion.tenant_id:
        # Reintento de un alta que falló: es la misma empresa, no se elige otra dirección.
        if existente is None or str(existente.id) != invitacion.tenant_id:
            raise ErrorDominio("PLA_INV_004", slug)
        if existente.estado == "aprovisionando":
            # Quedó a medias (se reinició el servidor): se marca para que el alta la retome.
            await run_in_threadpool(
                tenants.actualizar_estado, existente.id, "error", "Alta interrumpida."
            )
    elif existente is not None:
        raise ErrorDominio("TEN_VAL_001", slug)
    if not await run_in_threadpool(repositorios.iniciar_alta, invitacion.id, ahora_utc()):
        raise ErrorDominio("PLA_INV_000")


async def _dar_de_alta(invitacion: Invitacion, empresa: str, slug: str, nombre: str) -> None:
    async def avanzar(paso: str) -> None:
        if paso == "base_sql":
            # A partir de acá la empresa ya está en el catálogo: queda atada a la invitación.
            tenant = await run_in_threadpool(tenants.obtener_por_slug, slug)
            if tenant:
                await run_in_threadpool(
                    repositorios.vincular_empresa, invitacion.id, str(tenant.id)
                )
        await _guardar_progreso(invitacion.id, estado="en_curso", paso=paso, slug=slug)

    try:
        await tenants_servicios.aprovisionar(slug, empresa, invitacion.email, nombre, avanzar)
        await run_in_threadpool(repositorios.marcar_usada, invitacion.id)
        await _guardar_progreso(
            invitacion.id, estado="listo", slug=slug, url=correos.direccion_empresa(slug)
        )
        await registrar(
            "plataforma.empresa_registrada", invitacion.email, invitacion=invitacion.id, slug=slug
        )
    except Exception as error:
        log.exception("Falló el registro de la empresa %s.", slug)
        codigo = getattr(error, "codigo", "TEN_APR_000")
        await _guardar_progreso(invitacion.id, estado="error", codigo=codigo, slug=slug)
        await registrar(
            "plataforma.registro_fallido", invitacion.email, invitacion=invitacion.id, slug=slug
        )
    finally:
        await _redis().delete(_clave_bloqueo(invitacion.id))


async def estado(actual: SesionRegistro) -> dict:
    invitacion = await run_in_threadpool(repositorios.obtener_invitacion, actual.invitacion.id)
    avance = await progreso(actual.invitacion.id)
    # Sin bloqueo vigente, la réplica que corría el alta se reinició: se puede reintentar.
    if (
        invitacion.estado == "en_curso"
        and avance.get("estado") == "en_curso"
        and not await _redis().exists(_clave_bloqueo(invitacion.id))
    ):
        avance = {**avance, "estado": "interrumpido"}
    empresa = None
    if invitacion.tenant_id:
        tenant = await run_in_threadpool(tenants.obtener_por_id, invitacion.tenant_id)
        empresa = {"slug": tenant.slug, "nombre": tenant.nombre} if tenant else None
    return {
        "email": invitacion.email,
        "invitacion": invitacion.estado_visible(ahora_utc()),
        "vence_en": invitacion.vence_en.isoformat() + "Z",
        "empresa": empresa,
        "progreso": avance,
        "pasos": list(tenants_servicios.PASOS),
    }
