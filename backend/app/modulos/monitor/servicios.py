"""Casos de uso del monitoreo: dashboard (CU-000-003-A), umbrales (B) e histórico (C),
más backups (con su política) y jobs del SQL Server Agent."""

import logging
import re
from dataclasses import asdict
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
from app.modulos.monitor import analisis_ia, lectura, query_store, repositorios
from app.modulos.monitor.entidades import (
    LIMITES_FULL_HORAS,
    LIMITES_LOG_MINUTOS,
    METRICAS,
    PREFIJO_JOB,
    PREFIJO_JOB_BACKUP,
    PoliticaBackup,
    Umbral,
    agente_detenido,
    estado_backup,
    estado_job,
    falla_vigente,
)
from app.tenants.entidades import Tenant
from app.web.i18n import idioma_actual

log = logging.getLogger(__name__)

# Roles que configuran métricas; el resto del módulo es de lectura.
ROLES_CONFIGURAN = frozenset({"ADMINISTRADOR", "DBA"})

PATRON_JOB = re.compile(r"^[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$")
# Una explicación de la IA vale mientras no haya una ejecución nueva del job.
VIGENCIA_EXPLICACION_SEGUNDOS = 86_400

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
    estado = await repositorios.obtener_estado(str(tenant.id), conexion_id) or {
        "estado": "sin_datos"
    }
    politica = await obtener_politica(tenant, conexion_id)
    # El semáforo de backups y el estado de los jobs usan los mismos criterios que las
    # alertas.
    if estado.get("backups") is not None:
        estado["backups"] = [{**b, "estado": estado_backup(b, politica)} for b in estado["backups"]]
    if estado.get("jobs") is not None:
        estado["jobs"] = [
            {
                **j,
                "estado": estado_job(j),
                "alerta": falla_vigente(j),
                # Backups programados desde la app (WK-09 y WK-10).
                "backup_programado": j["nombre"].startswith(PREFIJO_JOB_BACKUP),
                "etiqueta": j["nombre"].removeprefix(PREFIJO_JOB),
            }
            for j in estado["jobs"]
        ]
    estado["agente_detenido"] = agente_detenido(estado.get("agente"))
    return {
        "conexion": {"id": conexion.id, "alias": conexion.alias, "servidor": servidor(conexion)},
        "umbrales": [u.__dict__ for u in await obtener_umbrales(tenant, conexion_id)],
        "unidades": {m.id: m.unidad for m in METRICAS.values()},
        "politica_backup": asdict(politica),
        **estado,
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


def _leer(c: Conexion, funcion, *argumentos, base: str | None = None):
    """Lectura en vivo de la instancia (fuera del recolector)."""
    try:
        with sqlserver.conectar_externo(
            servidor(c),
            c.usuario_sql,
            cifrado.descifrar(c.password_sql_cifrada),
            base or sqlserver.BASE_SISTEMA,
        ) as conexion:
            return funcion(conexion, *argumentos)
    except ErrorDominio:
        raise
    except Exception as error:
        raise ErrorDominio("MNT_DAT_000", str(error)) from error


def _leer_costosas(c: Conexion) -> list[dict]:
    return _leer(c, lectura.consultas_costosas)


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


# --- Backups -------------------------------------------------------------------------


async def obtener_politica(tenant: Tenant, conexion_id: str) -> PoliticaBackup:
    guardada = await run_in_threadpool(
        repositorios.obtener_politica, tenant.base_sqlserver, str(tenant.id), conexion_id
    )
    return guardada or PoliticaBackup()


async def guardar_politica(
    tenant: Tenant, actor: Sesion, conexion_id: str, politica: PoliticaBackup
) -> PoliticaBackup:
    if not puede_configurar(actor):
        raise ErrorDominio("USR_PER_000", "monitor.configurar")
    conexion = await conexiones.obtener(tenant, conexion_id)
    if not (
        LIMITES_FULL_HORAS[0] <= politica.full_horas <= LIMITES_FULL_HORAS[1]
        and LIMITES_LOG_MINUTOS[0] <= politica.log_minutos <= LIMITES_LOG_MINUTOS[1]
    ):
        raise ErrorDominio("MNT_CFG_001")
    anterior = await obtener_politica(tenant, conexion_id)
    await run_in_threadpool(
        repositorios.guardar_politica,
        tenant.base_sqlserver,
        str(tenant.id),
        conexion_id,
        politica,
    )
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="monitor.politica_backup_modificada",
            usuario=actor.email,
            tenant_id=str(tenant.id),
            detalle={
                "conexion_id": conexion_id,
                "alias": conexion.alias,
                "anterior": asdict(anterior),
                "nueva": asdict(politica),
            },
        ),
    )
    return politica


async def historial_backups(tenant: Tenant, conexion_id: str, base: str) -> list[dict]:
    conexion = await conexiones.obtener(tenant, conexion_id)
    if not base.strip() or len(base) > 128:
        raise ErrorDominio("USR_VAL_002", "Base inválida.")
    return await run_in_threadpool(_leer, conexion, lectura.historial_backups, base)


# --- Jobs del SQL Server Agent ------------------------------------------------------


async def historial_job(tenant: Tenant, conexion_id: str, job_id: str) -> list[dict]:
    conexion = await conexiones.obtener(tenant, conexion_id)
    if not PATRON_JOB.match(job_id):
        raise ErrorDominio("MNT_VAL_000", job_id)
    return await run_in_threadpool(_leer, conexion, lectura.historial_job, job_id)


async def explicar_job(tenant: Tenant, actor: Sesion, conexion_id: str, job_id: str) -> dict:
    """Explicación de la IA de la última falla del job (Administrador y DBA)."""
    if not puede_configurar(actor):
        raise ErrorDominio("USR_PER_000", "monitor.explicar")
    ejecuciones = await historial_job(tenant, conexion_id, job_id)
    fallida = next((e for e in ejecuciones if e["resultado"] == "falla"), None)
    if fallida is None:
        raise ErrorDominio("MNT_VAL_000", job_id)
    estado = await repositorios.obtener_estado(str(tenant.id), conexion_id) or {}
    nombre = next(
        (j["nombre"] for j in estado.get("jobs") or [] if j["id"].upper() == job_id.upper()),
        job_id,
    )
    # La misma falla no se vuelve a explicar (ni a pagar) mientras no haya otra ejecución.
    clave = (
        f"monitor:explicacion:{tenant.id}:{conexion_id}:{job_id}:{fallida['inicio']}"
        f":{idioma_actual()}"
    )
    guardada = await repositorios.obtener_de_cache(clave)
    if guardada:
        return guardada
    explicacion = await analisis_ia.explicar_falla(nombre, fallida)
    resultado = {"inicio": fallida["inicio"], **explicacion.model_dump()}
    await repositorios.guardar_en_cache(clave, resultado, VIGENCIA_EXPLICACION_SEGUNDOS)
    return resultado


# --- Consultas costosas (Query Store) -----------------------------------------------

# Query Store se lee en vivo: la caché evita repetir la lectura en cada refresco.
VIGENCIA_QUERY_STORE_SEGUNDOS = 300


async def _con_cache(clave: str, leer):
    guardado = await repositorios.obtener_de_cache(clave)
    if guardado is not None:
        return guardado
    valor = await leer()
    await repositorios.guardar_en_cache(clave, valor, VIGENCIA_QUERY_STORE_SEGUNDOS)
    return valor


def _validar_consulta(base: str, metrica: str, periodo: str) -> None:
    if (
        not base.strip()
        or len(base) > 128
        or metrica not in query_store.METRICAS
        or periodo not in query_store.PERIODOS_HORAS
    ):
        raise ErrorDominio("USR_VAL_002", "Base, métrica o período inválidos.")


async def estado_query_store(tenant: Tenant, conexion_id: str) -> dict:
    conexion = await conexiones.obtener(tenant, conexion_id)
    return await _con_cache(
        f"monitor:qs:estado:{tenant.id}:{conexion_id}",
        lambda: run_in_threadpool(_leer, conexion, query_store.estado_bases),
    )


async def consultas_query_store(
    tenant: Tenant, conexion_id: str, base: str, metrica: str, periodo: str
) -> dict:
    """Ranking de consultas de una base en el período, con su tendencia."""
    _validar_consulta(base, metrica, periodo)
    conexion = await conexiones.obtener(tenant, conexion_id)
    return await _con_cache(
        f"monitor:qs:{tenant.id}:{conexion_id}:{base}:{metrica}:{periodo}",
        lambda: run_in_threadpool(
            _leer, conexion, query_store.consultas_de_base, metrica, periodo, base=base
        ),
    )


async def consulta_query_store(
    tenant: Tenant, conexion_id: str, base: str, query_id: int, metrica: str, periodo: str
) -> dict:
    _validar_consulta(base, metrica, periodo)
    conexion = await conexiones.obtener(tenant, conexion_id)
    detalle = await run_in_threadpool(
        _leer,
        conexion,
        lambda abierta: query_store.detalle(abierta.cursor(), query_id, metrica, periodo),
        base=base,
    )
    if detalle is None:
        raise ErrorDominio("MNT_VAL_000", str(query_id))
    return detalle
