"""Casos de uso de alertas: reglas (CU-000-004-A y D), activas (B) y atención (C)."""

from dataclasses import replace
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from app.core.errores import ErrorDominio
from app.identidad.entidades import ROLES, Sesion
from app.modulos.alertas import repositorios
from app.modulos.alertas.entidades import (
    ACCIONES_ATENCION,
    OPERADORES,
    SEVERIDADES,
    Alerta,
    Regla,
)
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.conexiones import servicios as conexiones
from app.modulos.monitor.entidades import METRICAS
from app.tenants.entidades import Tenant

ROLES_CONFIGURAN = frozenset({"ADMINISTRADOR", "DBA"})
ROLES_ATIENDEN = frozenset({"DBA"})
MAXIMO_SOSTENIDA_SEGUNDOS = 24 * 60 * 60
LARGO_COMENTARIO = 1000


def permisos(sesion: Sesion) -> dict:
    return {
        "configurar": sesion.rol in ROLES_CONFIGURAN,
        "atender": sesion.rol in ROLES_ATIENDEN,
    }


def _exigir(sesion: Sesion, roles: frozenset[str], accion: str) -> None:
    if sesion.rol not in roles:
        raise ErrorDominio("USR_PER_000", accion)


async def _auditar(tenant: Tenant, actor: Sesion, accion: str, detalle: dict) -> None:
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion=accion, usuario=actor.email, tenant_id=str(tenant.id), detalle=detalle
        ),
    )


# --- Alertas ------------------------------------------------------------------------


async def listar(tenant: Tenant, estado: str) -> list[Alerta]:
    if estado not in ("activa", "atendida"):
        raise ErrorDominio("USR_VAL_002", "Estado inválido.")
    return await run_in_threadpool(
        repositorios.listar, tenant.base_sqlserver, str(tenant.id), estado
    )


async def resumen(tenant: Tenant) -> dict:
    cantidades = await run_in_threadpool(
        repositorios.contar_activas, tenant.base_sqlserver, str(tenant.id)
    )
    return {"activas": sum(cantidades.values()), **{s: cantidades.get(s, 0) for s in SEVERIDADES}}


async def obtener(tenant: Tenant, alerta_id: int) -> Alerta:
    alerta = await run_in_threadpool(
        repositorios.obtener, tenant.base_sqlserver, str(tenant.id), alerta_id
    )
    if alerta is None:
        raise ErrorDominio("ALR_VAL_000", str(alerta_id))
    return alerta


async def atender(
    tenant: Tenant, actor: Sesion, alerta_id: int, accion: str, comentario: str
) -> Alerta:
    _exigir(actor, ROLES_ATIENDEN, "alertas.atender")
    comentario = (comentario or "").strip()
    if accion not in ACCIONES_ATENCION or not comentario:
        raise ErrorDominio("USR_VAL_002", "Acción y comentario son obligatorios.")
    if len(comentario) > LARGO_COMENTARIO:
        raise ErrorDominio("USR_VAL_002", "Comentario demasiado largo.")
    alerta = await obtener(tenant, alerta_id)
    atendida = await run_in_threadpool(
        repositorios.atender,
        tenant.base_sqlserver,
        str(tenant.id),
        alerta_id,
        actor.email,
        accion,
        comentario,
    )
    if not atendida:
        raise ErrorDominio("ALR_VAL_002", str(alerta_id))
    await _auditar(
        tenant,
        actor,
        "alerta.atendida",
        {
            "alerta_id": alerta_id,
            "titulo": alerta.titulo,
            "severidad": alerta.severidad,
            "conexion": alerta.conexion_alias,
            "accion": accion,
            "comentario": comentario,
        },
    )
    return await obtener(tenant, alerta_id)


# --- Reglas -------------------------------------------------------------------------


async def listar_reglas(tenant: Tenant) -> list[Regla]:
    return await run_in_threadpool(
        repositorios.listar_reglas, tenant.base_sqlserver, str(tenant.id)
    )


async def _obtener_regla(tenant: Tenant, regla_id: str) -> Regla:
    try:
        UUID(regla_id)
    except ValueError:
        raise ErrorDominio("ALR_VAL_000", regla_id) from None
    regla = await run_in_threadpool(
        repositorios.obtener_regla, tenant.base_sqlserver, str(tenant.id), regla_id
    )
    if regla is None:
        raise ErrorDominio("ALR_VAL_000", regla_id)
    return regla


async def _validar(tenant: Tenant, r: Regla) -> Regla:
    nombre = (r.nombre or "").strip()
    destinatarios = tuple(dict.fromkeys(r.destinatarios))
    if (
        not nombre
        or len(nombre) > 100
        or r.metrica not in METRICAS
        or r.operador not in OPERADORES
        or r.severidad not in SEVERIDADES
        or not destinatarios
        or any(rol not in ROLES for rol in destinatarios)
        or not 0 <= r.sostenida_segundos <= MAXIMO_SOSTENIDA_SEGUNDOS
    ):
        raise ErrorDominio("USR_VAL_002", "Datos de la regla incompletos o inválidos.")
    if r.conexion_id:
        await conexiones.obtener(tenant, r.conexion_id)
    if await run_in_threadpool(
        repositorios.existe_nombre, tenant.base_sqlserver, str(tenant.id), nombre, r.id or None
    ):
        raise ErrorDominio("ALR_VAL_001", nombre)
    return replace(r, nombre=nombre, destinatarios=destinatarios)


async def crear_regla(tenant: Tenant, actor: Sesion, regla: Regla) -> Regla:
    _exigir(actor, ROLES_CONFIGURAN, "alertas.configurar")
    regla = await _validar(
        tenant, replace(regla, id="", tenant_id=str(tenant.id), creada_por=actor.email)
    )
    regla_id = await run_in_threadpool(
        repositorios.guardar_regla, tenant.base_sqlserver, regla, True
    )
    await _auditar(tenant, actor, "alerta.regla_creada", {"regla_id": regla_id, **_detalle(regla)})
    return await _obtener_regla(tenant, regla_id)


async def modificar_regla(tenant: Tenant, actor: Sesion, regla_id: str, regla: Regla) -> Regla:
    _exigir(actor, ROLES_CONFIGURAN, "alertas.configurar")
    actual = await _obtener_regla(tenant, regla_id)
    regla = await _validar(
        tenant,
        replace(regla, id=regla_id, tenant_id=str(tenant.id), creada_por=actual.creada_por),
    )
    await run_in_threadpool(repositorios.guardar_regla, tenant.base_sqlserver, regla, False)
    await _auditar(
        tenant,
        actor,
        "alerta.regla_modificada",
        {"regla_id": regla_id, "anterior": _detalle(actual), "nueva": _detalle(regla)},
    )
    return await _obtener_regla(tenant, regla_id)


async def eliminar_regla(tenant: Tenant, actor: Sesion, regla_id: str) -> int:
    _exigir(actor, ROLES_CONFIGURAN, "alertas.configurar")
    regla = await _obtener_regla(tenant, regla_id)
    cerradas = await run_in_threadpool(
        repositorios.eliminar_regla, tenant.base_sqlserver, str(tenant.id), regla_id, actor.email
    )
    await _auditar(
        tenant,
        actor,
        "alerta.regla_eliminada",
        {"regla_id": regla_id, **_detalle(regla), "alertas_cerradas": cerradas},
    )
    return cerradas


def _detalle(r: Regla) -> dict:
    return {
        "nombre": r.nombre,
        "metrica": r.metrica,
        "condicion": f"{r.operador} {r.valor:g}",
        "severidad": r.severidad,
        "conexion_id": r.conexion_id,
        "sostenida_segundos": r.sostenida_segundos,
        "destinatarios": list(r.destinatarios),
        "push": r.push,
        "activa": r.activa,
    }
