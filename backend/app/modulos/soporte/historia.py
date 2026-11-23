"""Historia de la base de la aplicación: estructura y datos a través del tiempo.

Estructura: un snapshot del catálogo (el mismo que usa el Histórico de Schema de las
instancias) se toma al arrancar el backend, después de aplicar las migraciones, y a pedido
del Administrador. Si cambió respecto de la última versión se guarda una nueva, con el
origen del cambio: migraciones de la aplicación o un cambio hecho por fuera. El detalle de
cada sentencia (quién, cuándo, desde qué programa) lo registra el trigger DDL de la base.

Datos: consulta de las tablas temporales (solo lectura): la tabla tal como estaba en un
momento o todas las versiones de sus filas.
"""

import logging
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

from starlette.concurrency import run_in_threadpool

from app.core.errores import ErrorDominio
from app.identidad.entidades import Sesion
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.historico_schema import captura
from app.modulos.soporte import repositorios
from app.modulos.soporte.entidades import (
    DDL_POR_PAGINA,
    LIMITE_FILAS,
    MODO_CAMBIOS,
    MODO_MOMENTO,
    ORDEN_SCRIPT,
    ORIGEN_DETECTADO,
    ORIGEN_INICIAL,
    ORIGEN_MANUAL,
    ORIGEN_MIGRACION,
    TABLAS_CON_HISTORIA,
)
from app.tenants.entidades import Tenant
from app.web.i18n import formatear

log = logging.getLogger(__name__)
ACTUAL = "actual"


def _fecha(valor: datetime | None) -> str | None:
    if valor is None:
        return None
    return (valor if valor.tzinfo else valor.replace(tzinfo=UTC)).isoformat()


# --- Estructura -------------------------------------------------------------------


async def registrar_estructura(
    tenant: Tenant, origen: str | None = None, usuario: str | None = None
) -> int | None:
    """Guarda una versión nueva si la estructura cambió. Devuelve su número o None.

    Sin ``origen`` (arranque del backend) se deduce: migraciones nuevas desde la versión
    anterior o un cambio hecho por fuera de la aplicación.
    """
    objetos = await run_in_threadpool(repositorios.capturar, tenant.base_sqlserver)
    aplicadas = await run_in_threadpool(repositorios.migraciones_aplicadas, tenant.base_sqlserver)
    anterior = await repositorios.ultima_version(tenant.base_mongodb, str(tenant.id))
    cambios = captura.diferencias(anterior["objetos"], objetos) if anterior else None
    if cambios == []:
        return None
    nuevas = (
        sorted(set(aplicadas) - set(anterior.get("migraciones_aplicadas", []))) if anterior else []
    )
    if anterior is None:
        origen = ORIGEN_INICIAL
    elif origen is None:
        origen = ORIGEN_MIGRACION if nuevas else ORIGEN_DETECTADO
    return await repositorios.guardar_version(
        tenant.base_mongodb,
        {
            "tenant_id": str(tenant.id),
            "base": tenant.base_sqlserver,
            "origen": origen,
            "usuario": usuario,
            "migraciones": nuevas,
            "migraciones_aplicadas": aplicadas,
            "cambios": cambios,
            "objetos": objetos,
        },
    )


async def registrar_estructura_todo(tenants: list[Tenant]) -> None:
    """Al arrancar: una versión nueva por cada tenant cuya estructura cambió."""
    for tenant in tenants:
        try:
            numero = await registrar_estructura(tenant)
        except Exception:
            log.exception("No se pudo registrar la estructura del tenant %s.", tenant.slug)
            continue
        if numero:
            log.info("Estructura de %s: versión %s.", tenant.base_sqlserver, numero)


async def capturar_ahora(tenant: Tenant, sesion: Sesion) -> dict:
    numero = await registrar_estructura(tenant, ORIGEN_MANUAL, sesion.email)
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="soporte.estructura_capturada",
            usuario=sesion.email,
            tenant_id=str(tenant.id),
            detalle={"base": tenant.base_sqlserver, "version": numero},
        ),
    )
    return {"version": numero}


def _resumen(cambios: list[dict] | None) -> dict:
    conteo = {"agregado": 0, "eliminado": 0, "modificado": 0}
    for cambio in cambios or []:
        conteo[cambio["operacion"]] += 1
    return conteo


def _version(d: dict, completa: bool = False) -> dict:
    salida = {
        "version": d["version"],
        "ts": _fecha(d["ts"]),
        "origen": d["origen"],
        "usuario": d.get("usuario"),
        "migraciones": d.get("migraciones") or [],
        "inicial": d.get("cambios") is None,
        "cambios": len(d.get("cambios") or []),
        "resumen": _resumen(d.get("cambios")),
    }
    if completa:
        salida["detalle"] = d.get("cambios") or []
    return salida


async def versiones(tenant: Tenant) -> dict:
    filas = await repositorios.listar_versiones(tenant.base_mongodb, str(tenant.id))
    return {"base": tenant.base_sqlserver, "versiones": [_version(d) for d in filas]}


async def _obtener(tenant: Tenant, numero: int) -> dict:
    documento = await repositorios.obtener_version(tenant.base_mongodb, str(tenant.id), numero)
    if documento is None:
        raise ErrorDominio("SCH_SNP_000", f"{tenant.base_sqlserver} v{numero}")
    return documento


async def detalle(tenant: Tenant, numero: int) -> dict:
    return _version(await _obtener(tenant, numero), completa=True)


async def comparar(tenant: Tenant, desde: int, hasta: str) -> dict:
    if hasta != ACTUAL and not hasta.isdigit():
        raise ErrorDominio("USR_VAL_002", "Versión inválida.")
    if str(desde) == hasta:
        raise ErrorDominio("USR_VAL_002", "Elegí dos versiones distintas.")
    origen = (await _obtener(tenant, desde))["objetos"]
    if hasta == ACTUAL:
        destino = await run_in_threadpool(repositorios.capturar, tenant.base_sqlserver)
    else:
        destino = (await _obtener(tenant, int(hasta)))["objetos"]
    cambios = captura.diferencias(origen, destino)
    return {"desde": desde, "hasta": hasta, "resumen": _resumen(cambios), "cambios": cambios}


async def script(tenant: Tenant, numero: int) -> str:
    """Estructura completa de una versión, para recrearla: tablas, índices y código."""
    documento = await _obtener(tenant, numero)
    objetos = sorted(
        documento["objetos"].values(),
        key=lambda o: (
            ORDEN_SCRIPT.index(o["tipo"]) if o["tipo"] in ORDEN_SCRIPT else 99,
            o["nombre"],
        ),
    )
    titulo = formatear("soporte.script.titulo", base=tenant.base_sqlserver, version=numero)
    renglones = [f"-- {titulo}", f"-- {_fecha(documento['ts'])}", ""]
    for objeto in objetos:
        renglones += [f"-- {objeto['tipo']}: {objeto['nombre']}", objeto["definicion"], "GO", ""]
    return "\n".join(renglones)


async def historial_ddl(tenant: Tenant, pagina: int, texto: str | None) -> dict:
    pagina = max(pagina, 1)
    filas, total = await run_in_threadpool(
        repositorios.historial_ddl,
        tenant.base_sqlserver,
        (texto or "").strip() or None,
        (pagina - 1) * DDL_POR_PAGINA,
        DDL_POR_PAGINA,
    )
    return {
        "registros": [{**f, "ts": _fecha(f["ts"])} for f in filas],
        "total": total,
        "pagina": pagina,
        "paginas": max(1, -(-total // DDL_POR_PAGINA)),
    }


async def ddl(tenant: Tenant, ddl_id: int) -> dict:
    fila = await run_in_threadpool(repositorios.ddl, tenant.base_sqlserver, ddl_id)
    if fila is None:
        raise ErrorDominio("USR_VAL_002", "Registro inexistente.")
    return {**fila, "ts": _fecha(fila["ts"])}


# --- Datos ------------------------------------------------------------------------


def _valor(valor):
    """Valores de SQL Server a algo que se pueda mostrar en la pantalla."""
    if isinstance(valor, datetime):
        # Las columnas del período están en UTC; el resto de las fechas de la app también.
        return _fecha(valor)
    if isinstance(valor, date | time):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, uuid.UUID):
        return str(valor)
    if isinstance(valor, bytes):
        return valor.hex()
    return valor


def tablas() -> list[str]:
    return list(TABLAS_CON_HISTORIA)


async def datos(
    tenant: Tenant, tabla: str, modo: str, momento: datetime | None, clave: str | None
) -> dict:
    if tabla not in TABLAS_CON_HISTORIA:
        raise ErrorDominio("USR_VAL_002", "Tabla inválida.")
    if modo not in (MODO_MOMENTO, MODO_CAMBIOS):
        raise ErrorDominio("USR_VAL_002", "Modo inválido.")
    definicion = await run_in_threadpool(
        repositorios.tabla_con_historia, tenant.base_sqlserver, tabla
    )
    if definicion is None:
        raise ErrorDominio("USR_VAL_002", "La tabla no tiene historia.")
    clave = (clave or "").strip() or None
    if modo == MODO_MOMENTO:
        if momento is None:
            raise ErrorDominio("USR_VAL_002", "Indicá el momento.")
        utc = (momento if momento.tzinfo else momento.replace(tzinfo=UTC)).astimezone(UTC)
        columnas, filas = await run_in_threadpool(
            repositorios.filas_al_momento,
            tenant.base_sqlserver,
            definicion,
            str(tenant.id),
            utc.replace(tzinfo=None),
            clave,
        )
    else:
        columnas, filas = await run_in_threadpool(
            repositorios.cambios, tenant.base_sqlserver, definicion, str(tenant.id), clave
        )
    return {
        "tabla": tabla,
        "clave": list(definicion.clave),
        "columnas": columnas,
        "filas": [[_valor(v) for v in fila] for fila in filas],
        "limite": LIMITE_FILAS,
        "truncado": len(filas) >= LIMITE_FILAS,
    }
