"""Casos de uso del Histórico de Schema (CU-000-008 A y B; la restauración, en espera).

Registra los cambios de estructura ejecutados desde la aplicación (Promover desde el
Sandbox y workarounds propios). Lo hecho fuera de la app queda fuera de alcance
(decisión documentada en docs/arquitectura.md). Antes de ejecutar un DDL se toma un
snapshot de la base y, al terminar, otro: la diferencia entre ambos es exactamente lo
que cambió esa ejecución.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from starlette.concurrency import run_in_threadpool

from app.core import cifrado
from app.core.errores import ErrorDominio
from app.datos import sqlserver
from app.modulos.chat_ia.analizador import analizar
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import Conexion
from app.modulos.historico_schema import captura, repositorios
from app.tenants.entidades import Tenant

log = logging.getLogger(__name__)
ACTUAL = "actual"
SEPARADOR_LOTES = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass
class Captura:
    """Estado de las bases alcanzadas por un DDL antes de ejecutarlo."""

    conexion: Conexion
    antes: dict[str, dict | None] = field(default_factory=dict)


def _capturar(conexion: Conexion, base: str) -> dict:
    with sqlserver.conectar_externo(
        conexiones.servidor_de(conexion),
        conexion.usuario_sql,
        cifrado.descifrar(conexion.password_sql_cifrada),
        base,
        timeout=15,
    ) as abierta:
        return captura.capturar(abierta)


async def _snapshot(conexion: Conexion, base: str) -> dict | None:
    """Snapshot o None: una falla no debe impedir la operación que se está auditando."""
    try:
        return await run_in_threadpool(_capturar, conexion, base)
    except Exception:
        log.warning("SCH_SNP_000 no se pudo leer la estructura de %s en %s", base, conexion.alias)
        return None


async def preparar(conexion: Conexion, base: str | None, sql: str) -> Captura | None:
    """Toma el snapshot previo si el T-SQL cambia estructura; si no, devuelve None."""
    if not base:
        return None
    # Los scripts del Sandbox y de los workarounds pueden tener varios lotes (GO).
    analisis = [analizar(lote, base) for lote in SEPARADOR_LOTES.split(sql or "") if lote.strip()]
    if not any("DDL" in a.acciones for a in analisis):
        return None
    bases = {base.lower(): base}
    for tabla in (t for a in analisis for t in a.tablas):
        bases.setdefault(tabla.base.lower(), tabla.base)
    return Captura(conexion, {b: await _snapshot(conexion, b) for b in bases.values()})


async def registrar(
    tenant: Tenant,
    previa: Captura | None,
    ddl: str,
    usuario: str,
    origen: str,
    referencia: str,
    autorizado_por: str | None = None,
) -> list[int]:
    """Guarda una versión por cada base que cambió. Devuelve los números de versión."""
    if previa is None:
        return []
    numeros = []
    for base, antes in previa.antes.items():
        despues = await _snapshot(previa.conexion, base)
        cambios = captura.diferencias(antes, despues) if antes is not None and despues else None
        if cambios == []:
            continue  # el DDL no alteró la estructura (por ejemplo, un ALTER INDEX REBUILD)
        numeros.append(
            await repositorios.guardar(
                tenant.base_mongodb,
                {
                    "tenant_id": str(tenant.id),
                    "conexion_id": previa.conexion.id,
                    "conexion_alias": previa.conexion.alias,
                    "ambiente": previa.conexion.ambiente,
                    "base": base,
                    "usuario": usuario,
                    "autorizado_por": autorizado_por,
                    "origen": origen,
                    "referencia": referencia,
                    "ddl": ddl,
                    "cambios": cambios,
                    "objetos": despues,
                },
            )
        )
    return numeros


# --- Consulta (CU-000-008-A) --------------------------------------------------------


def _fecha(valor: datetime) -> str:
    return (valor if valor.tzinfo else valor.replace(tzinfo=UTC)).isoformat()


def _resumen(cambios: list[dict] | None) -> dict:
    conteo = {"agregado": 0, "eliminado": 0, "modificado": 0}
    for cambio in cambios or []:
        conteo[cambio["operacion"]] += 1
    return conteo


def _version(d: dict, completa: bool = False) -> dict:
    salida = {
        "version": d["version"],
        "ts": _fecha(d["ts"]),
        "usuario": d["usuario"],
        "autorizado_por": d.get("autorizado_por"),
        "origen": d["origen"],
        "referencia": d.get("referencia"),
        "ddl": d["ddl"],
        "sin_diff": d.get("cambios") is None,
        "cambios": len(d.get("cambios") or []),
        "resumen": _resumen(d.get("cambios")),
    }
    if completa:
        salida["detalle"] = d.get("cambios") or []
    return salida


async def versiones(tenant: Tenant, conexion_id: str, base: str) -> dict:
    conexion = await conexiones.obtener(tenant, conexion_id)
    filas = await repositorios.listar(tenant.base_mongodb, str(tenant.id), conexion.id, base)
    return {
        "conexion": conexion.alias,
        "ambiente": conexion.ambiente,
        "base": base,
        "versiones": [_version(d) for d in filas],
    }


async def _obtener(tenant: Tenant, conexion_id: str, base: str, version: int) -> dict:
    documento = await repositorios.obtener(
        tenant.base_mongodb, str(tenant.id), conexion_id, base, version
    )
    if documento is None:
        raise ErrorDominio("SCH_SNP_000", f"{base} v{version}")
    return documento


async def detalle(tenant: Tenant, conexion_id: str, base: str, version: int) -> dict:
    await conexiones.obtener(tenant, conexion_id)
    return _version(await _obtener(tenant, conexion_id, base, version), completa=True)


# --- Comparación (CU-000-008-B) -----------------------------------------------------


async def comparar(
    tenant: Tenant, conexion_id: str, base: str, desde: int, hasta: int | str
) -> dict:
    conexion = await conexiones.obtener(tenant, conexion_id)
    if desde == hasta:
        raise ErrorDominio("USR_VAL_002", "Elegí dos versiones distintas.")
    origen = await _obtener(tenant, conexion.id, base, desde)
    if hasta == ACTUAL:
        destino = await _snapshot(conexion, base)
    else:
        destino = (await _obtener(tenant, conexion.id, base, int(hasta))).get("objetos")
    if origen.get("objetos") is None or destino is None:
        raise ErrorDominio("SCH_SNP_000", base)
    cambios = captura.diferencias(origen["objetos"], destino)
    return {
        "desde": desde,
        "hasta": hasta,
        "base": base,
        "conexion": conexion.alias,
        "resumen": _resumen(cambios),
        "cambios": cambios,
    }


def como_script(comparacion: dict) -> str:
    """Reporte de diferencias en texto, para descargar."""
    renglones = [
        f"-- Diferencias de schema · {comparacion['conexion']} · {comparacion['base']}",
        f"-- v{comparacion['desde']} → {comparacion['hasta']}",
        "",
    ]
    for cambio in comparacion["cambios"]:
        renglones.append(f"-- {cambio['operacion'].upper()}: {cambio['tipo']} {cambio['nombre']}")
        if cambio["antes"]:
            renglones += ["-- Antes:", *[f"--   {r}" for r in cambio["antes"].splitlines()]]
        if cambio["despues"]:
            renglones += ["-- Después:", cambio["despues"]]
        renglones.append("")
    return "\n".join(renglones)
