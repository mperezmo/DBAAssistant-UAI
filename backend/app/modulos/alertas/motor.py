"""Motor de alertas: aplica las condiciones a cada lectura del recolector.

- Una condición dispara cuando se cumple durante su tiempo sostenido.
- Mientras siga activa se actualiza el último valor (como máximo una vez por minuto) y
  sube de severidad si corresponde; no se repite el push salvo al escalar.
- Cuando deja de cumplirse la alerta queda "normalizada", pero sigue activa hasta que
  un DBA la atienda (CU-000-004-C).
- Si alguien la atiende mientras la condición persiste, no se vuelve a disparar hasta
  que la condición se normalice y reaparezca.
- Las condiciones desestimadas por un DBA no generan alertas mientras estén vigentes.
"""

import logging
import time
from typing import Protocol

from starlette.concurrency import run_in_threadpool

from app.datos import sqlserver
from app.modulos.alertas import condiciones, notificaciones, repositorios
from app.modulos.alertas.entidades import Alerta, Condicion, rango_severidad
from app.modulos.conexiones.entidades import Conexion
from app.modulos.monitor import repositorios as monitor
from app.modulos.monitor.entidades import Lectura
from app.tenants.entidades import Tenant

VIGENCIA_CONFIGURACION_SEGUNDOS = 30
REFRESCO_ALERTA_SEGUNDOS = 60


class Automatizador(Protocol):
    async def alerta_disparada(
        self, tenant: Tenant, conexion: Conexion, alerta: Alerta
    ) -> None: ...


class Motor:
    def __init__(self, automatizador: Automatizador | None = None) -> None:
        # Ejecución automática de workarounds configurada por un DBA (opcional).
        self._automatizador = automatizador
        # (conexion_id, clave) → momento en que la condición empezó a cumplirse.
        self._desde: dict[tuple[str, str], float] = {}
        # (conexion_id, clave) → (severidad, último refresco) de las alertas en curso.
        self._en_curso: dict[tuple[str, str], tuple[str, float]] = {}
        self._tenants_cargados: set[str] = set()
        self._configuracion: dict[str, tuple[float, list, dict, set]] = {}

    async def evaluar(self, tenant: Tenant, conexion: Conexion, lectura: Lectura | None) -> None:
        ahora = time.monotonic()
        await self._cargar_en_curso(tenant)
        reglas, umbrales, desestimadas = await self._configuracion_de(tenant, ahora)
        servidor = sqlserver.servidor_externo(conexion.host, conexion.puerto, conexion.instancia)
        cumplidas, completa = condiciones.evaluar(
            lectura, servidor, umbrales.get(conexion.id, {}), reglas, conexion.id
        )
        # Lo desestimado se ignora y se olvida: al reactivarse, si la condición sigue,
        # dispara como una alerta nueva.
        for c in [c for c in cumplidas if (conexion.id, c.clave) in desestimadas]:
            cumplidas.remove(c)
            self._desde.pop((conexion.id, c.clave), None)
            self._en_curso.pop((conexion.id, c.clave), None)

        vigentes = {c.clave for c in cumplidas}
        for c in cumplidas:
            await self._procesar(tenant, conexion, servidor, c, ahora)

        for clave_desde in [k for k in self._desde if k[0] == conexion.id and k[1] not in vigentes]:
            if completa or clave_desde[1] == condiciones.CLAVE_INSTANCIA:
                del self._desde[clave_desde]
        for clave in [k for k in self._en_curso if k[0] == conexion.id and k[1] not in vigentes]:
            if completa or clave[1] == condiciones.CLAVE_INSTANCIA:
                del self._en_curso[clave]
                await run_in_threadpool(
                    repositorios.normalizar,
                    tenant.base_sqlserver,
                    str(tenant.id),
                    conexion.id,
                    clave[1],
                )

    async def _procesar(
        self, tenant: Tenant, conexion: Conexion, servidor: str, c: Condicion, ahora: float
    ) -> None:
        clave = (conexion.id, c.clave)
        inicio = self._desde.setdefault(clave, ahora)
        if ahora - inicio < c.sostenida_segundos:
            return
        datos = (conexion.alias, servidor, conexion.ambiente)
        en_curso = self._en_curso.get(clave)
        if en_curso is None:
            resultado = await run_in_threadpool(
                repositorios.disparar,
                tenant.base_sqlserver,
                str(tenant.id),
                conexion.id,
                datos,
                c,
                False,
            )
        else:
            severidad, refrescada = en_curso
            escala = rango_severidad(c.severidad) > rango_severidad(severidad)
            if not escala and ahora - refrescada < REFRESCO_ALERTA_SEGUNDOS:
                return
            # Solo actualiza: si ya la atendieron, no se vuelve a crear.
            resultado = await run_in_threadpool(
                repositorios.disparar,
                tenant.base_sqlserver,
                str(tenant.id),
                conexion.id,
                datos,
                c,
                True,
            )
        if resultado is None:
            self._en_curso[clave] = (c.severidad, ahora)
            return
        self._en_curso[clave] = (resultado.alerta.severidad, ahora)
        if (c.push or c.email) and (resultado.nueva or resultado.escalo):
            await notificaciones.notificar(tenant, resultado.alerta, c)
        if resultado.nueva and self._automatizador:
            try:
                await self._automatizador.alerta_disparada(tenant, conexion, resultado.alerta)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Falla en la ejecución automática ante la alerta %s", resultado.alerta.id
                )

    async def _cargar_en_curso(self, tenant: Tenant) -> None:
        """Al arrancar se retoman las alertas activas para poder normalizarlas."""
        tenant_id = str(tenant.id)
        if tenant_id in self._tenants_cargados:
            return
        activas = await run_in_threadpool(
            repositorios.claves_activas, tenant.base_sqlserver, tenant_id
        )
        for conexion_id, clave, severidad in activas:
            self._en_curso[(conexion_id, clave)] = (severidad, 0.0)
        self._tenants_cargados.add(tenant_id)

    async def _configuracion_de(self, tenant: Tenant, ahora: float) -> tuple[list, dict, set]:
        tenant_id = str(tenant.id)
        guardada = self._configuracion.get(tenant_id)
        if guardada and ahora - guardada[0] < VIGENCIA_CONFIGURACION_SEGUNDOS:
            return guardada[1:]
        reglas = await run_in_threadpool(
            repositorios.listar_reglas, tenant.base_sqlserver, tenant_id
        )
        umbrales = await run_in_threadpool(
            monitor.listar_umbrales_del_tenant, tenant.base_sqlserver, tenant_id
        )
        desestimadas = {
            (d.conexion_id, d.clave)
            for d in await run_in_threadpool(
                repositorios.listar_desestimadas, tenant.base_sqlserver, tenant_id
            )
        }
        self._configuracion[tenant_id] = (ahora, reglas, umbrales, desestimadas)
        return reglas, umbrales, desestimadas

    def invalidar_configuracion(self) -> None:
        self._configuracion.clear()
