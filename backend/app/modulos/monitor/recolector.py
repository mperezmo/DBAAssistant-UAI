"""Recolector de métricas en segundo plano.

Cada ``monitor_intervalo_segundos`` lee todas las conexiones de todos los tenants
activos, guarda cada lectura en MongoDB (histórico) y la última en Redis (dashboard).
Funciona aunque nadie tenga el dashboard abierto, para que el histórico sea continuo y
las alertas puedan evaluarse en segundo plano.

Si el backend corre en varias réplicas, un candado en Redis garantiza que recolecte
solo una por vez; si esa réplica cae, otra toma el candado al vencer.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pyodbc
from starlette.concurrency import run_in_threadpool

from app.core import cifrado
from app.core.config import obtener_configuracion
from app.datos import cache, sqlserver
from app.modulos.conexiones import repositorios as conexiones
from app.modulos.conexiones.entidades import Conexion
from app.modulos.monitor import lectura, repositorios
from app.tenants import repositorios as tenants
from app.tenants.entidades import Tenant

log = logging.getLogger(__name__)

CLAVE_CANDADO = "monitor:recolector"
VIGENCIA_CANDADO_SEGUNDOS = 15
# Las conexiones nuevas o modificadas se incorporan en, como máximo, este tiempo.
VIGENCIA_CATALOGO_SEGUNDOS = 30
TIMEOUT_CONEXION_SEGUNDOS = 3
HILOS = 8


def _ahora() -> str:
    return datetime.now(UTC).isoformat()


class Recolector:
    def __init__(self) -> None:
        self._id = uuid.uuid4().hex
        self._hilos = ThreadPoolExecutor(max_workers=HILOS, thread_name_prefix="monitor")
        # Por conexión: conexión ODBC abierta (con la huella de sus datos), estado entre
        # lecturas y última lectura correcta.
        self._abiertas: dict[str, tuple[tuple, pyodbc.Connection]] = {}
        self._estados: dict[str, lectura.EstadoLectura] = {}
        self._ultimas: dict[str, dict] = {}
        self._en_curso: set[str] = set()
        self._tareas: set[asyncio.Task] = set()
        self._catalogo: list[tuple[Tenant, Conexion]] = []
        self._catalogo_en = 0.0

    async def ejecutar(self) -> None:
        intervalo = obtener_configuracion().monitor_intervalo_segundos
        log.info("Recolector de métricas iniciado (cada %s s).", intervalo)
        while True:
            inicio = time.monotonic()
            try:
                if await self._tomar_candado():
                    await self.ciclo()
            except Exception:
                log.exception("Falla en el ciclo del recolector de métricas.")
            await asyncio.sleep(max(0.0, intervalo - (time.monotonic() - inicio)))

    async def _tomar_candado(self) -> bool:
        redis = cache.cliente()
        if await redis.set(CLAVE_CANDADO, self._id, nx=True, ex=VIGENCIA_CANDADO_SEGUNDOS):
            return True
        if await redis.get(CLAVE_CANDADO) == self._id:
            await redis.expire(CLAVE_CANDADO, VIGENCIA_CANDADO_SEGUNDOS)
            return True
        return False

    async def ciclo(self) -> None:
        objetivos = await self._objetivos()
        # Cada conexión se procesa en su propia tarea: una instancia lenta o caída no
        # demora la lectura de las demás.
        for tenant, conexion in objetivos:
            if conexion.id not in self._en_curso:
                self._en_curso.add(conexion.id)
                tarea = asyncio.create_task(self._recolectar(tenant, conexion))
                # Se guarda la referencia para que la tarea no se descarte antes de terminar.
                self._tareas.add(tarea)
                tarea.add_done_callback(self._tareas.discard)

    async def _objetivos(self) -> list[tuple[Tenant, Conexion]]:
        if time.monotonic() - self._catalogo_en >= VIGENCIA_CATALOGO_SEGUNDOS:
            self._catalogo = await run_in_threadpool(self._leer_catalogo)
            self._catalogo_en = time.monotonic()
            self._cerrar_ajenas({c.id for _, c in self._catalogo})
        return self._catalogo

    @staticmethod
    def _leer_catalogo() -> list[tuple[Tenant, Conexion]]:
        objetivos = []
        for tenant in tenants.listar():
            if tenant.estado != "activo":
                continue
            try:
                objetivos += [
                    (tenant, c) for c in conexiones.listar(tenant.base_sqlserver, str(tenant.id))
                ]
            except Exception:
                log.exception("No se pudieron leer las conexiones del tenant %s.", tenant.slug)
        return objetivos

    async def _recolectar(self, tenant: Tenant, conexion: Conexion) -> None:
        try:
            ts = datetime.now(UTC)
            try:
                resultado = await asyncio.get_running_loop().run_in_executor(
                    self._hilos, self._leer, conexion
                )
            except Exception as error:
                await self._registrar_falla(tenant, conexion, error)
                return
            estado = {
                "estado": "ok",
                "ts": ts.isoformat(),
                "ultima_lectura": ts.isoformat(),
                "valores": resultado.valores,
                "bloqueos": resultado.bloqueos,
                "volumenes": resultado.volumenes,
            }
            if self._ultimas.get(conexion.id, {}).get("estado") == "sin_conexion":
                log.info("Se recuperó la conexión con %s.", conexion.alias)
            self._ultimas[conexion.id] = estado
            await repositorios.guardar_estado(str(tenant.id), conexion.id, estado)
            await repositorios.insertar_muestra(
                tenant.base_mongodb, conexion.id, ts, resultado.valores
            )
        except Exception:
            log.exception("No se pudo guardar la lectura de %s.", conexion.alias)
        finally:
            self._en_curso.discard(conexion.id)

    async def _registrar_falla(self, tenant: Tenant, conexion: Conexion, error: Exception) -> None:
        anterior = self._ultimas.get(conexion.id, {})
        if anterior.get("estado") != "sin_conexion":
            # Se informa una vez por caída para no llenar el log cada pocos segundos.
            log.warning("CON_DB_003 %s (%s): %s", conexion.alias, conexion.host, error)
        estado = {
            **anterior,
            "estado": "sin_conexion",
            "ts": _ahora(),
            "error": "CON_DB_003",
        }
        self._ultimas[conexion.id] = estado
        await repositorios.guardar_estado(str(tenant.id), conexion.id, estado)

    def _leer(self, conexion: Conexion) -> lectura.Lectura:
        abierta = self._abrir(conexion)
        try:
            estado = self._estados.setdefault(conexion.id, lectura.EstadoLectura())
            return lectura.leer(abierta, estado, time.monotonic())
        except Exception:
            # Una conexión que falló no se reutiliza: se abre otra en el próximo ciclo.
            self._cerrar(conexion.id)
            raise

    def _abrir(self, conexion: Conexion) -> pyodbc.Connection:
        huella = (
            conexion.host,
            conexion.puerto,
            conexion.instancia,
            conexion.usuario_sql,
            conexion.password_sql_cifrada,
        )
        actual = self._abiertas.get(conexion.id)
        if actual and actual[0] == huella:
            return actual[1]
        self._cerrar(conexion.id)
        abierta = sqlserver.abrir_externo(
            sqlserver.servidor_externo(conexion.host, conexion.puerto, conexion.instancia),
            conexion.usuario_sql,
            cifrado.descifrar(conexion.password_sql_cifrada),
            timeout=TIMEOUT_CONEXION_SEGUNDOS,
        )
        self._abiertas[conexion.id] = (huella, abierta)
        return abierta

    def _cerrar(self, conexion_id: str) -> None:
        actual = self._abiertas.pop(conexion_id, None)
        if actual:
            with contextlib.suppress(pyodbc.Error):
                actual[1].close()

    def _cerrar_ajenas(self, vigentes: set[str]) -> None:
        for conexion_id in set(self._abiertas) - vigentes - self._en_curso:
            self._cerrar(conexion_id)
            self._estados.pop(conexion_id, None)
            self._ultimas.pop(conexion_id, None)

    def detener(self) -> None:
        for conexion_id in list(self._abiertas):
            self._cerrar(conexion_id)
        self._hilos.shutdown(wait=False, cancel_futures=True)
