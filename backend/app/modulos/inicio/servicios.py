"""Resumen operativo del panel principal (diseño: PageDashboard).

Todo se calcula con datos que la aplicación ya guarda; no se consulta ninguna instancia:
conexiones, alertas y workarounds (SQL Server), última lectura del recolector (Redis),
histórico de métricas y bitácora de Sandbox (MongoDB).
"""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool

from app.core.errores import ErrorDominio
from app.identidad.entidades import AMBIENTES, Sesion
from app.modulos.chat_ia.analizador import analizar
from app.modulos.conexiones.entidades import ZONA_HORARIA_POR_DEFECTO
from app.modulos.inicio import repositorios
from app.modulos.monitor import repositorios as monitor
from app.modulos.workarounds import integrados
from app.tenants.entidades import Tenant

log = logging.getLogger(__name__)

TODOS = "TODOS"
BLOQUEO_PROLONGADO_SEGUNDOS = 30
VENTANA_LATENCIA = timedelta(hours=1)
VENTANA_PENDIENTES = timedelta(days=7)


def inicio_de_mes(ahora: datetime) -> datetime:
    """Comienzo del mes calendario en la hora local (Buenos Aires), expresado en UTC."""
    local = ahora.astimezone(ZoneInfo(ZONA_HORARIA_POR_DEFECTO))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def _utc(valor: datetime) -> datetime:
    """SQL Server devuelve las fechas UTC sin zona."""
    return valor.replace(tzinfo=UTC) if valor.tzinfo is None else valor


def disponibilidad(
    conexiones: list[dict], caidas: list[dict], desde: datetime, ahora: datetime
) -> float | None:
    """Porcentaje medio de tiempo con conexión de cada instancia en el período.

    Cada instancia se mide desde que se registró (o desde ``desde``). Una caída que sigue
    abierta cuenta hasta ``ahora``.
    """
    porcentajes = []
    for conexion in conexiones:
        inicio = max(desde, _utc(conexion["creado_en"]))
        periodo = (ahora - inicio).total_seconds()
        if periodo <= 0:
            continue
        tramos = sorted(
            (max(inicio, _utc(c["desde"])), min(ahora, _utc(c["hasta"]) if c["hasta"] else ahora))
            for c in caidas
            if c["conexion_id"] == conexion["id"]
        )
        caido, fin = 0.0, inicio
        for comienzo, final in tramos:
            # Los tramos superpuestos se cuentan una sola vez.
            comienzo = max(comienzo, fin)
            if final > comienzo:
                caido += (final - comienzo).total_seconds()
                fin = final
        porcentajes.append(100 * (1 - caido / periodo))
    if not porcentajes:
        return None
    return round(sum(porcentajes) / len(porcentajes), 1)


async def _estado_actual(tenant: Tenant, ids: list[str]) -> dict:
    online = sesiones = bloqueos = 0
    for conexion_id in ids:
        try:
            estado = await monitor.obtener_estado(str(tenant.id), conexion_id)
        except Exception as error:
            log.warning("Sin la última lectura de %s: %s", conexion_id, error)
            estado = None
        if not estado or estado.get("estado") != "ok":
            continue
        online += 1
        sesiones += int((estado.get("valores") or {}).get("sesiones") or 0)
        bloqueos += sum(
            1
            for b in estado.get("bloqueos") or []
            if (b.get("espera_s") or 0) > BLOQUEO_PROLONGADO_SEGUNDOS
        )
    return {"online": online, "sesiones": sesiones, "bloqueos_prolongados": bloqueos}


async def resumen(
    tenant: Tenant, sesion: Sesion, ambiente: str, ahora: datetime | None = None
) -> dict:
    if ambiente not in (TODOS, *AMBIENTES):
        raise ErrorDominio("USR_VAL_002", "Ambiente inválido.")
    ahora = ahora or datetime.now(UTC)
    ambientes = list(AMBIENTES) if ambiente == TODOS else [ambiente]
    mes = inicio_de_mes(ahora)
    base, tenant_id = tenant.base_sqlserver, str(tenant.id)

    conexiones = [
        c
        for c in await run_in_threadpool(repositorios.conexiones, base, tenant_id)
        if c["ambiente"] in ambientes
    ]
    ids = [c["id"] for c in conexiones]
    alertas = [
        a
        for a in await run_in_threadpool(repositorios.alertas_activas, base, tenant_id)
        if a["ambiente"] in ambientes
    ]
    caidas = await run_in_threadpool(
        repositorios.caidas,
        base,
        tenant_id,
        mes.replace(tzinfo=None),
        ahora.astimezone(UTC).replace(tzinfo=None),
    )
    try:
        latencia = await repositorios.latencia_p95(
            tenant.base_mongodb, ids, ahora - VENTANA_LATENCIA
        )
    except Exception as error:
        log.warning("No se pudo calcular la latencia p95: %s", error)
        latencia = None

    datos = {
        "ambiente": ambiente,
        "instancias": {
            "total": len(conexiones),
            "nuevas_mes": sum(1 for c in conexiones if _utc(c["creado_en"]) >= mes),
        },
        "alertas": {
            "activas": sum(a["cantidad"] for a in alertas),
            "criticas": sum(a["cantidad"] for a in alertas if a["severidad"] == "critical"),
        },
        "salud": {
            "disponibilidad": disponibilidad(conexiones, caidas, mes, ahora),
            "total": len(conexiones),
            "latencia_p95": round(latencia) if latencia is not None else None,
            **await _estado_actual(tenant, ids),
        },
        "workarounds": None,
        "pendientes_promocion": None,
    }

    if sesion.puede_ver("workarounds"):
        propios = [
            w
            for w in await run_in_threadpool(repositorios.workarounds_propios, base, tenant_id)
            if set(w["ambientes"]) & set(ambientes)
        ]
        disponibles = [i for i in integrados.CATALOGO.values() if set(i.ambientes) & set(ambientes)]
        datos["workarounds"] = {
            "disponibles": len(disponibles) + len(propios),
            "nuevos_mes": sum(1 for w in propios if _utc(w["creado_en"]) >= mes),
        }
    if sesion.puede_ver("sandbox"):
        # Solo cuentan las que modifican algo: una lectura validada no se promueve.
        validadas = await repositorios.sql_pendientes_de_promocion(
            tenant.base_mongodb, tenant_id, ambientes, ahora - VENTANA_PENDIENTES
        )
        datos["pendientes_promocion"] = sum(
            1 for sql, base_datos in validadas if not analizar(sql, base_datos).es_lectura
        )
    return datos
