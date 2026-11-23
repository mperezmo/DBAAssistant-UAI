"""Casos de uso de Soporte (solo Administrador): bitácoras técnicas, salud de los
servicios y estado de las colas y procesos en segundo plano.

Las bitácoras del sistema y de excepciones usan el motor de Auditoría; la historia de la
base de la aplicación está en ``historia``.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from starlette.concurrency import run_in_threadpool

from app.core import excepciones, procesos
from app.core.config import obtener_configuracion
from app.core.secretos import proveedor_secretos
from app.datos import cache, mongo, sqlserver
from app.identidad.auth0 import ClienteAuth0
from app.identidad.entidades import Sesion
from app.modulos.auditoria import servicios as auditoria
from app.modulos.sandbox import cliente as sandbox
from app.modulos.soporte.entidades import TOLERANCIA_LATIDOS
from app.tenants.entidades import Tenant
from app.web.i18n import traducir

log = logging.getLogger(__name__)
GRUPO = auditoria.BITACORAS_SOPORTE


async def resumen_bitacoras(tenant: Tenant, sesion: Sesion) -> dict:
    return {
        "bitacoras": auditoria.bitacoras_visibles(sesion, GRUPO),
        "totales": await auditoria.totales(tenant, sesion, GRUPO),
        "nivel_excepciones": await auditoria.nivel_excepciones(tenant),
        "puede_configurar_nivel": sesion.rol in auditoria.ROLES_CONFIGURAN_NIVEL,
    }


# --- Salud de los servicios -------------------------------------------------------


async def _medir(verificar: Callable[[], Awaitable[None]]) -> tuple[str, int | None]:
    inicio = time.perf_counter()
    try:
        await verificar()
    except Exception as error:  # el panel muestra el servicio caído sin cortar el resto
        log.warning("Servicio no disponible: %s", error)
        return "error", None
    return "online", round((time.perf_counter() - inicio) * 1000)


async def _sqlserver() -> None:
    await run_in_threadpool(sqlserver.verificar)


def _configurado(secreto: str) -> bool:
    try:
        return bool(proveedor_secretos().obtener(secreto))
    except Exception:
        return False


async def servicios(tenant: Tenant) -> list[dict]:
    cfg = obtener_configuracion()
    tarjetas: list[tuple[str, Callable[[], Awaitable[None]] | None, str]] = [
        ("SQL Server", _sqlserver, tenant.base_sqlserver),
        ("MongoDB", mongo.verificar, tenant.base_mongodb),
        ("Redis", cache.verificar, traducir("servicios.redis.detalle")),
        (
            "Auth0",
            ClienteAuth0(cfg).verificar if cfg.auth0_configurado else None,
            cfg.auth0_domain,
        ),
        (
            "Sandbox",
            sandbox.salud if _configurado("sandbox_token") else None,
            traducir("servicios.sandbox.detalle"),
        ),
    ]
    resultado = []
    for nombre, verificar, detalle in tarjetas:
        if verificar is None:
            estado, latencia, detalle = "warning", None, traducir("servicios.no_configurado")
        else:
            estado, latencia = await _medir(verificar)
        resultado.append(
            {"nombre": nombre, "estado": estado, "latencia_ms": latencia, "detalle": detalle}
        )
    return resultado


# --- Colas y procesos en segundo plano --------------------------------------------


def _estado_proceso(nombre: str, habilitado: bool, ahora: datetime) -> dict:
    datos = procesos.latidos().get(nombre)
    if not habilitado:
        return {"nombre": nombre, "estado": "desactivado"}
    if datos is None:
        return {"nombre": nombre, "estado": "sin_datos"}
    atraso = (ahora - datos["ts"]).total_seconds()
    vigente = atraso <= datos["intervalo_segundos"] * TOLERANCIA_LATIDOS
    estado = ("activo" if datos["activo"] else "en_espera") if vigente else "detenido"
    return {
        "nombre": nombre,
        "estado": estado,
        "ultimo": datos["ts"].isoformat(),
        **{k: v for k, v in datos.items() if k not in ("ts", "activo")},
    }


def colas_y_procesos(ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(UTC)
    cfg = obtener_configuracion()
    cola = excepciones.estadisticas()
    if cola and cola["ultima_falla"]:
        cola["ultima_falla"] = cola["ultima_falla"].isoformat()
    return {
        "colas": [{"nombre": "bitacora_logs", **cola}] if cola else [],
        "procesos": [
            _estado_proceso("recolector", cfg.monitor_recolector_activo, ahora),
            _estado_proceso("programador_sandbox", cfg.sandbox_programador_activo, ahora),
        ],
    }
