"""Sincronización diaria de la estructura de cada servidor en su Sandbox.

Cada minuto revisa las conexiones: si ya pasó la hora configurada (hora local de la
conexión) y el Sandbox no se sincronizó desde entonces, lo sincroniza. Igual que el
recolector, un candado en Redis evita que lo hagan dos réplicas a la vez.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool

from app.datos import cache
from app.modulos.conexiones import repositorios as conexiones
from app.modulos.conexiones.entidades import Conexion
from app.modulos.sandbox import repositorios, servicios
from app.tenants import repositorios as tenants

log = logging.getLogger(__name__)

CLAVE_CANDADO = "sandbox:programador"
INTERVALO_SEGUNDOS = 60


def ultimo_horario(conexion: Conexion, ahora: datetime) -> datetime:
    """Último momento (UTC, sin zona) en que correspondía sincronizar esta conexión."""
    zona = ZoneInfo(conexion.zona_horaria)
    local = ahora.astimezone(zona)
    programado = datetime.combine(local.date(), conexion.sandbox_sincronizacion, tzinfo=zona)
    if programado > local:
        programado -= timedelta(days=1)
    return programado.astimezone(UTC).replace(tzinfo=None)


class Programador:
    def __init__(self) -> None:
        self._id = uuid.uuid4().hex

    async def ejecutar(self) -> None:
        while True:
            try:
                if await self._tomar_candado():
                    await self.revisar(datetime.now(UTC))
            except Exception:
                log.exception("Falla en el programador del Sandbox.")
            await asyncio.sleep(INTERVALO_SEGUNDOS)

    async def _tomar_candado(self) -> bool:
        redis = cache.cliente()
        if await redis.set(CLAVE_CANDADO, self._id, nx=True, ex=INTERVALO_SEGUNDOS * 2):
            return True
        if await redis.get(CLAVE_CANDADO) == self._id:
            await redis.expire(CLAVE_CANDADO, INTERVALO_SEGUNDOS * 2)
            return True
        return False

    async def revisar(self, ahora: datetime) -> None:
        for tenant in await run_in_threadpool(tenants.listar):
            if tenant.estado != "activo":
                continue
            revisadas: set[str] = set()
            for conexion in await run_in_threadpool(
                conexiones.listar, tenant.base_sqlserver, str(tenant.id)
            ):
                clave = servicios.clave_de(tenant, conexion)
                # Varias conexiones al mismo servidor comparten el Sandbox.
                if clave in revisadas:
                    continue
                revisadas.add(clave)
                fila = await run_in_threadpool(
                    repositorios.obtener, tenant.base_sqlserver, str(tenant.id), clave
                )
                if fila and fila["estado"] == "sincronizando":
                    continue
                if (
                    fila
                    and fila["sincronizado_en"]
                    and fila["sincronizado_en"] >= ultimo_horario(conexion, ahora)
                ):
                    continue
                log.info("Sincronización diaria del Sandbox %s", clave)
                await servicios.sincronizar(tenant, None, conexion.id)
