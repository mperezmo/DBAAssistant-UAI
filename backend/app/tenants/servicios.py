"""Aprovisionamiento automático de tenants.

``aprovisionar`` deja a un cliente listo para operar en un solo paso: registro
en el catálogo, base SQL Server con su estructura, base MongoDB con sus
colecciones y primer Administrador. Cada paso es idempotente, por lo que
volver a ejecutarlo sobre un tenant con error retoma desde donde falló.
"""

import logging

from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.datos import migraciones, mongo, sqlserver
from app.identidad.auth0 import ClienteAuth0
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.tenants import repositorios
from app.tenants.entidades import Tenant, nombres_de_bases, validar_slug

log = logging.getLogger(__name__)

USUARIO_SISTEMA = "sistema"


def migrar_catalogo() -> None:
    cfg = obtener_configuracion()
    sqlserver.crear_base_si_no_existe(cfg.sqlserver_base_catalogo)
    migraciones.aplicar(cfg.sqlserver_base_catalogo, "catalogo")


def migrar_todo() -> None:
    """Aplica los cambios de estructura pendientes al catálogo y a cada tenant."""
    migrar_catalogo()
    for tenant in repositorios.listar():
        if tenant.estado in ("activo", "suspendido"):
            migraciones.aplicar(tenant.base_sqlserver, "tenant")


async def preparar_mongo_todo() -> None:
    """Aplica colecciones e índices vigentes (p. ej. la retención de métricas) a cada tenant."""
    for tenant in await run_in_threadpool(repositorios.listar):
        if tenant.estado in ("activo", "suspendido"):
            try:
                await mongo.preparar_base_tenant(tenant.base_mongodb)
            except Exception:
                log.exception("No se pudo preparar MongoDB del tenant %s.", tenant.slug)


async def aprovisionar(slug: str, nombre: str, email_administrador: str) -> Tenant:
    validar_slug(slug)
    if not nombre.strip() or "@" not in email_administrador:
        raise ErrorDominio("USR_VAL_002", "Nombre y email del Administrador son obligatorios.")

    await run_in_threadpool(migrar_catalogo)

    cfg = obtener_configuracion()
    tenant = await run_in_threadpool(repositorios.obtener_por_slug, slug)
    falta_organizacion = (
        tenant is not None and cfg.auth0_configurado and not tenant.auth0_organizacion_id
    )
    if tenant and tenant.estado != "error" and not falta_organizacion:
        raise ErrorDominio("TEN_VAL_001", slug)
    if tenant is None:
        base_sql, base_mongo = nombres_de_bases(slug, cfg)
        tenant = await run_in_threadpool(repositorios.crear, slug, nombre, base_sql, base_mongo)

    try:
        await run_in_threadpool(sqlserver.crear_base_si_no_existe, tenant.base_sqlserver)
        await run_in_threadpool(migraciones.aplicar, tenant.base_sqlserver, "tenant")
        await mongo.preparar_base_tenant(tenant.base_mongodb)
        await run_in_threadpool(repositorios.crear_administrador, tenant, email_administrador)
        if cfg.auth0_configurado and not tenant.auth0_organizacion_id:
            # El nombre de la organización es el slug: Auth0 lo usa en las URLs de retorno.
            auth0 = ClienteAuth0(cfg)
            await auth0.registrar_direcciones(f"https://{slug}.{cfg.dominio_base}")
            organizacion = await auth0.crear_organizacion(slug, nombre)
            await run_in_threadpool(repositorios.actualizar_organizacion, tenant.id, organizacion)
        elif not cfg.auth0_configurado:
            log.warning("Auth0 no configurado: el tenant %s queda sin organización", slug)
    except Exception as error:
        await run_in_threadpool(repositorios.actualizar_estado, tenant.id, "error", str(error))
        raise ErrorDominio("TEN_APR_000", str(error)) from error

    await run_in_threadpool(repositorios.actualizar_estado, tenant.id, "activo")
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="tenant.aprovisionado",
            usuario=USUARIO_SISTEMA,
            tenant_id=str(tenant.id),
            detalle={"slug": slug, "administrador": email_administrador},
        ),
    )
    log.info("Tenant %s aprovisionado", slug)
    return await run_in_threadpool(repositorios.obtener_por_slug, slug)


async def sincronizar_auth0(slug: str) -> str:
    """Vuelve a registrar en Auth0 las direcciones de acceso de un tenant existente."""
    cfg = obtener_configuracion()
    tenant = await run_in_threadpool(repositorios.obtener_por_slug, slug)
    if tenant is None:
        raise ErrorDominio("TEN_VAL_000", slug)
    url_base = f"https://{slug}.{cfg.dominio_base}"
    await ClienteAuth0(cfg).registrar_direcciones(url_base)
    return url_base
