"""Casos de uso del monitoreo: dashboard (CU-000-003-A), umbrales (B) e histórico (C)."""

import logging
from datetime import UTC, datetime, timedelta

from starlette.concurrency import run_in_threadpool

from app.core import cifrado
from app.core.errores import ErrorDominio
from app.datos import sqlserver
from app.identidad.entidades import Sesion
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import Conexion
from app.modulos.monitor import lectura, repositorios
from app.modulos.monitor.entidades import METRICAS, Umbral
from app.tenants.entidades import Tenant

log = logging.getLogger(__name__)

# Roles que configuran métricas; el resto del módulo es de lectura.
ROLES_CONFIGURAN = frozenset({"ADMINISTRADOR", "DBA"})

# Rango del histórico → (duración, tamaño de cada tramo del gráfico) en segundos.
RANGOS = {
    "1h": (3600, 60),
    "24h": (86_400, 900),
    "7d": (604_800, 7200),
    "30d": (2_592_000, 21_600),
}


def puede_configurar(sesion: Sesion) -> bool:
    return sesion.rol in ROLES_CONFIGURAN


def servidor(c: Conexion) -> str:
    return sqlserver.servidor_externo(c.host, c.puerto, c.instancia)


async def listar_conexiones(tenant: Tenant) -> list[dict]:
    return [
        {"id": c.id, "alias": c.alias, "ambiente": c.ambiente, "servidor": servidor(c)}
        for c in await conexiones.listar(tenant)
    ]


async def obtener_umbrales(tenant: Tenant, conexion_id: str) -> list[Umbral]:
    guardados = await run_in_threadpool(
        repositorios.listar_umbrales, tenant.base_sqlserver, str(tenant.id), conexion_id
    )
    return [guardados.get(m) or Umbral.por_defecto(m) for m in METRICAS]


async def estado_actual(tenant: Tenant, conexion_id: str) -> dict:
    """Última lectura de la instancia con su configuración de métricas.

    Si la instancia dejó de responder, el recolector conserva los últimos valores y
    los marca como obsoletos (``estado = sin_conexion``).
    """
    conexion = await conexiones.obtener(tenant, conexion_id)
    estado = await repositorios.obtener_estado(str(tenant.id), conexion_id)
    return {
        "conexion": {"id": conexion.id, "alias": conexion.alias, "servidor": servidor(conexion)},
        "umbrales": [u.__dict__ for u in await obtener_umbrales(tenant, conexion_id)],
        "unidades": {m.id: m.unidad for m in METRICAS.values()},
        **(estado or {"estado": "sin_datos"}),
    }


async def serie(tenant: Tenant, conexion_id: str, rango: str, metricas: list[str]) -> dict:
    if rango not in RANGOS or not metricas or any(m not in METRICAS for m in metricas):
        raise ErrorDominio("USR_VAL_002", "Rango o métricas inválidos.")
    await conexiones.obtener(tenant, conexion_id)
    duracion, intervalo = RANGOS[rango]
    desde = datetime.now(UTC) - timedelta(seconds=duracion)
    try:
        puntos = await repositorios.serie(
            tenant.base_mongodb, conexion_id, metricas, desde, intervalo
        )
    except Exception as error:
        raise ErrorDominio("MNT_DAT_000", str(error)) from error
    return {"rango": rango, "intervalo": intervalo, "desde": desde.isoformat(), "puntos": puntos}


def _leer_costosas(c: Conexion) -> list[dict]:
    try:
        with sqlserver.conectar_externo(
            servidor(c), c.usuario_sql, cifrado.descifrar(c.password_sql_cifrada)
        ) as conexion:
            return lectura.consultas_costosas(conexion)
    except Exception as error:
        raise ErrorDominio("MNT_DAT_000", str(error)) from error


async def consultas_costosas(tenant: Tenant, conexion_id: str) -> list[dict]:
    """Ranking leído en vivo, con caché corta para no repetir la consulta en cada refresco."""
    conexion = await conexiones.obtener(tenant, conexion_id)
    clave = f"monitor:costosas:{tenant.id}:{conexion_id}"
    en_cache = await repositorios.obtener_de_cache(clave)
    if en_cache is not None:
        return en_cache
    consultas = await run_in_threadpool(_leer_costosas, conexion)
    await repositorios.guardar_en_cache(clave, consultas)
    return consultas


def _validar(umbral: Umbral) -> Umbral:
    if umbral.metrica not in METRICAS:
        raise ErrorDominio("USR_VAL_002", f"Métrica desconocida: {umbral.metrica}")
    warning, critical = umbral.warning, umbral.critical
    # Ambos umbrales o ninguno (la métrica se muestra sin colores).
    if (warning is None) != (critical is None):
        raise ErrorDominio("USR_VAL_002", f"Faltan umbrales de {umbral.metrica}.")
    if warning is not None and (warning < 0 or critical <= warning):
        raise ErrorDominio("MNT_CFG_000", umbral.metrica)
    return umbral


async def guardar_umbrales(
    tenant: Tenant, actor: Sesion, conexion_id: str, umbrales: list[Umbral]
) -> list[Umbral]:
    if not puede_configurar(actor):
        raise ErrorDominio("USR_PER_000", "monitor.configurar")
    conexion = await conexiones.obtener(tenant, conexion_id)
    validados = [_validar(u) for u in umbrales]
    if len({u.metrica for u in validados}) != len(validados):
        raise ErrorDominio("USR_VAL_002", "Métricas repetidas.")
    anteriores = await obtener_umbrales(tenant, conexion_id)
    await run_in_threadpool(
        repositorios.guardar_umbrales,
        tenant.base_sqlserver,
        str(tenant.id),
        conexion_id,
        validados,
    )
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="monitor.umbrales_modificados",
            usuario=actor.email,
            tenant_id=str(tenant.id),
            detalle={
                "conexion_id": conexion_id,
                "alias": conexion.alias,
                "anteriores": [u.__dict__ for u in anteriores],
                "nuevos": [u.__dict__ for u in validados],
            },
        ),
    )
    return await obtener_umbrales(tenant, conexion_id)
