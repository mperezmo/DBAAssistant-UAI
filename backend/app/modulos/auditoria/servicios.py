"""Casos de uso de Auditoría (CU-000-009 A, B, C y D).

Tres bitácoras en MongoDB, retenidas 5 años: transacciones (lo ejecutado sobre las
instancias), auditoría del sistema (seguridad y configuración) y excepciones (logs).
"""

import csv
import io
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bson import ObjectId
from bson.errors import InvalidId

from app.core import excepciones
from app.core.errores import ErrorDominio
from app.identidad.entidades import AMBIENTES, Sesion
from app.modulos.auditoria import reporte_pdf, repositorios
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.conexiones.entidades import ZONA_HORARIA_POR_DEFECTO
from app.tenants.entidades import Tenant
from app.web.i18n import formatear, traducir

ZONA = ZoneInfo(ZONA_HORARIA_POR_DEFECTO)
TAMANO_PAGINA = 25


@dataclass(frozen=True)
class Bitacora:
    coleccion: str
    roles: frozenset[str]
    # Columnas de las exportaciones: (clave de traducción del título, campo de la fila).
    columnas: tuple[tuple[str, str], ...]


BITACORAS = {
    "transacciones": Bitacora(
        "bitacora_transacciones",
        frozenset({"ADMINISTRADOR", "DBA"}),
        (
            ("auditoria.col.fecha", "fecha"),
            ("auditoria.col.usuario", "usuario"),
            ("auditoria.col.ip", "ip"),
            ("auditoria.col.accion", "accion"),
            ("auditoria.col.ambiente", "ambiente"),
            ("auditoria.col.entidad", "entidad"),
            ("auditoria.col.filas", "filas"),
            ("auditoria.col.resultado", "resultado_texto"),
            ("auditoria.col.sql", "sql"),
        ),
    ),
    # CU-000-009-B: solo el Administrador.
    "sistema": Bitacora(
        "bitacora_auditoria",
        frozenset({"ADMINISTRADOR"}),
        (
            ("auditoria.col.fecha", "fecha"),
            ("auditoria.col.usuario", "usuario"),
            ("auditoria.col.ip", "ip"),
            ("auditoria.col.accion", "accion"),
            ("auditoria.col.detalle", "resumen"),
        ),
    ),
    "excepciones": Bitacora(
        excepciones.COLECCION,
        frozenset({"ADMINISTRADOR", "DBA"}),
        (
            ("auditoria.col.fecha", "fecha"),
            ("auditoria.col.nivel", "nivel"),
            ("auditoria.col.modulo", "modulo"),
            ("auditoria.col.codigo", "codigo"),
            ("auditoria.col.usuario", "usuario"),
            ("auditoria.col.mensaje", "mensaje"),
        ),
    ),
}
LIMITES_EXPORTACION = {"csv": 1_000_000, "pdf": 10_000}
ROLES_CONFIGURAN_NIVEL = frozenset({"ADMINISTRADOR"})


@dataclass(frozen=True)
class Filtros:
    desde: date | None = None
    hasta: date | None = None
    usuario: str | None = None
    ambiente: str | None = None
    tipo: str | None = None  # prefijo de la acción (o nivel, en excepciones)
    texto: str | None = None


def bitacoras_visibles(sesion: Sesion) -> list[str]:
    return [nombre for nombre, b in BITACORAS.items() if sesion.rol in b.roles]


def _bitacora(sesion: Sesion, nombre: str) -> Bitacora:
    bitacora = BITACORAS.get(nombre)
    if bitacora is None:
        raise ErrorDominio("USR_VAL_002", "Bitácora inválida.")
    if sesion.rol not in bitacora.roles:
        raise ErrorDominio("USR_PER_000", f"bitácora {nombre}")
    return bitacora


def _inicio_del_dia(dia: date) -> datetime:
    return datetime.combine(dia, time.min, ZONA).astimezone(UTC)


def _contiene(texto: str) -> dict:
    return {"$regex": re.escape(texto.strip()), "$options": "i"}


def filtro_mongo(tenant: Tenant, nombre: str, f: Filtros) -> dict:
    """Traduce los filtros de la pantalla a una consulta, siempre acotada al tenant."""
    filtro: dict = (
        {"tenant": tenant.slug} if nombre == "excepciones" else {"tenant_id": str(tenant.id)}
    )
    rango = {}
    if f.desde:
        rango["$gte"] = _inicio_del_dia(f.desde)
    if f.hasta:
        rango["$lt"] = _inicio_del_dia(f.hasta + timedelta(days=1))
    if rango:
        filtro["ts"] = rango
    if f.usuario and f.usuario.strip():
        filtro["usuario"] = _contiene(f.usuario)
    if f.ambiente:
        if f.ambiente not in AMBIENTES:
            raise ErrorDominio("USR_VAL_002", "Ambiente inválido.")
        filtro["detalle.ambiente"] = f.ambiente
    if f.tipo:
        if nombre == "excepciones":
            filtro["nivel"] = f.tipo.upper()
        else:
            filtro["accion"] = {"$regex": f"^{re.escape(f.tipo)}\\."}
    if f.texto and f.texto.strip():
        campos = {
            "transacciones": (
                "accion",
                "detalle.sql",
                "detalle.conexion",
                "detalle.conexion_alias",
                "detalle.workaround_nombre",
                "detalle.base",
            ),
            "sistema": ("accion", "detalle.alias", "detalle.email", "detalle.nombre"),
            "excepciones": ("mensaje", "codigo", "modulo"),
        }[nombre]
        filtro["$or"] = [{campo: _contiene(f.texto)} for campo in campos]
    return filtro


# --- Normalización de los registros -------------------------------------------------


def _fecha(valor: datetime | None) -> str | None:
    if valor is None:
        return None
    return (valor if valor.tzinfo else valor.replace(tzinfo=UTC)).isoformat()


def _fecha_local(valor: datetime | None) -> str:
    if valor is None:
        return ""
    return (
        (valor if valor.tzinfo else valor.replace(tzinfo=UTC))
        .astimezone(ZONA)
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def _json(valor):
    """Valores de MongoDB (fechas, ObjectId) a algo que se pueda mostrar o exportar."""
    if isinstance(valor, dict):
        return {k: _json(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_json(v) for v in valor]
    if isinstance(valor, datetime):
        return _fecha(valor)
    if isinstance(valor, ObjectId):
        return str(valor)
    return valor


def _accion_transaccion(d: dict) -> str:
    detalle = d.get("detalle") or {}
    etiquetas = {
        "chat.consulta_ejecutada": f"{detalle.get('tipo', 'SQL')} (chat)",
        "chat.exportado": f"EXPORT {str(detalle.get('formato', 'csv')).upper()}",
        "sandbox.promovido": traducir("auditoria.accion.promovida"),
        "workaround.ejecutado": f"WORKAROUND {detalle.get('workaround', '')}".strip(),
    }
    return etiquetas.get(d["accion"], d["accion"])


def _resultado(codigo: str) -> str:
    """Estado de la operación en el idioma de quien lo ve (el código queda para el color)."""
    clave = f"auditoria.resultado.{codigo}"
    texto = traducir(clave)
    return codigo if texto == clave else texto


def fila(nombre: str, d: dict) -> dict:
    """Registro de cualquier bitácora con los campos que muestra el listado."""
    base = {"id": str(d["_id"]), "ts": _fecha(d.get("ts")), "fecha": _fecha_local(d.get("ts"))}
    if nombre == "excepciones":
        return {
            **base,
            **{k: d.get(k) for k in ("nivel", "modulo", "codigo", "usuario", "mensaje", "ruta")},
        }
    detalle = d.get("detalle") or {}
    if nombre == "sistema":
        resumen = json.dumps(_json(detalle), ensure_ascii=False, default=str)
        return {
            **base,
            "usuario": d.get("usuario"),
            "ip": d.get("ip"),
            "accion": d["accion"],
            "resumen": resumen[:300],
        }
    conexion = detalle.get("conexion") or detalle.get("conexion_alias")
    entidad = " · ".join(
        str(p) for p in (conexion, detalle.get("base"), detalle.get("workaround_nombre")) if p
    )
    filas = next(
        (detalle[k] for k in ("filas_afectadas", "filas", "total") if detalle.get(k) is not None),
        None,
    )
    resultado = detalle.get("estado") or "exitosa"
    return {
        **base,
        "usuario": d.get("usuario"),
        "ip": d.get("ip"),
        "accion": _accion_transaccion(d),
        "ambiente": detalle.get("ambiente"),
        "entidad": entidad,
        "filas": filas,
        "resultado": resultado,
        "resultado_texto": _resultado(resultado),
        "sql": detalle.get("sql"),
        "promocion": bool(detalle.get("promocion")),
        "mobile": bool(detalle.get("mobile")),
    }


# --- Consulta (CU-000-009 A, B y C) -------------------------------------------------


async def listar(
    tenant: Tenant, sesion: Sesion, nombre: str, filtros: Filtros, pagina: int
) -> dict:
    bitacora = _bitacora(sesion, nombre)
    pagina = max(pagina, 1)
    documentos, total = await repositorios.consultar(
        tenant.base_mongodb,
        bitacora.coleccion,
        filtro_mongo(tenant, nombre, filtros),
        (pagina - 1) * TAMANO_PAGINA,
        TAMANO_PAGINA,
    )
    return {
        "registros": [fila(nombre, d) for d in documentos],
        "total": total,
        "pagina": pagina,
        "paginas": max(1, -(-total // TAMANO_PAGINA)),
    }


async def totales(tenant: Tenant, sesion: Sesion) -> dict:
    return {
        nombre: await repositorios.contar(
            tenant.base_mongodb,
            BITACORAS[nombre].coleccion,
            filtro_mongo(tenant, nombre, Filtros()),
        )
        for nombre in bitacoras_visibles(sesion)
    }


async def detalle(tenant: Tenant, sesion: Sesion, nombre: str, registro_id: str) -> dict:
    bitacora = _bitacora(sesion, nombre)
    try:
        oid = ObjectId(registro_id)
    except InvalidId, TypeError:
        raise ErrorDominio("USR_VAL_002", "Registro inválido.") from None
    documento = await repositorios.obtener(
        tenant.base_mongodb,
        bitacora.coleccion,
        {"_id": oid, **filtro_mongo(tenant, nombre, Filtros())},
    )
    if documento is None:
        raise ErrorDominio("USR_VAL_002", "Registro inexistente.")
    return {
        **fila(nombre, documento),
        "documento": _json({k: v for k, v in documento.items() if k not in ("_id", "tenant_id")}),
    }


# --- Exportación (CU-000-009-D) -----------------------------------------------------


async def preparar_exportacion(
    tenant: Tenant, sesion: Sesion, nombre: str, filtros: Filtros, formato: str
) -> tuple[Bitacora, dict, int]:
    bitacora = _bitacora(sesion, nombre)
    if formato not in LIMITES_EXPORTACION:
        raise ErrorDominio("USR_VAL_002", "Formato inválido.")
    filtro = filtro_mongo(tenant, nombre, filtros)
    cantidad = await repositorios.contar(tenant.base_mongodb, bitacora.coleccion, filtro)
    if cantidad > LIMITES_EXPORTACION[formato]:
        raise ErrorDominio(
            "EXP_LIM_000", None, {"registros": cantidad, "limite": LIMITES_EXPORTACION[formato]}
        )
    return bitacora, filtro, cantidad


async def _auditar_exportacion(
    tenant: Tenant, sesion: Sesion, nombre: str, filtros: Filtros, formato: str, filas: int
) -> None:
    await repositorios.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="auditoria.exportado",
            usuario=sesion.email,
            tenant_id=str(tenant.id),
            detalle={
                "bitacora": nombre,
                "formato": formato,
                "filas": filas,
                "filtros": {k: str(v) for k, v in filtros.__dict__.items() if v},
            },
        ),
    )


def _celda(valor) -> str:
    return "" if valor is None else str(valor)


async def exportar_csv(
    tenant: Tenant, sesion: Sesion, nombre: str, filtros: Filtros
) -> AsyncIterator[str]:
    bitacora, filtro, _ = await preparar_exportacion(tenant, sesion, nombre, filtros, "csv")

    async def lineas():
        salida = io.StringIO()
        escritor = csv.writer(salida)
        salida.write("﻿")
        escritor.writerow([traducir(titulo) for titulo, _ in bitacora.columnas])
        exportadas = 0
        async for documento in repositorios.iterar(tenant.base_mongodb, bitacora.coleccion, filtro):
            registro = fila(nombre, documento)
            escritor.writerow([_celda(registro.get(campo)) for _, campo in bitacora.columnas])
            exportadas += 1
            if salida.tell() > 64_000:
                yield salida.getvalue()
                salida.seek(0)
                salida.truncate()
        yield salida.getvalue()
        await _auditar_exportacion(tenant, sesion, nombre, filtros, "csv", exportadas)

    return lineas()


async def exportar_pdf(tenant: Tenant, sesion: Sesion, nombre: str, filtros: Filtros) -> bytes:
    bitacora, filtro, _ = await preparar_exportacion(tenant, sesion, nombre, filtros, "pdf")
    filas = [
        fila(nombre, d)
        async for d in repositorios.iterar(tenant.base_mongodb, bitacora.coleccion, filtro)
    ]
    contenido = reporte_pdf.generar(
        titulo=formatear("auditoria.pdf.titulo", bitacora=traducir(f"auditoria.bitacora.{nombre}")),
        empresa=tenant.nombre,
        generado_por=sesion.email,
        filtros={k: str(v) for k, v in filtros.__dict__.items() if v},
        columnas=tuple((traducir(titulo), campo) for titulo, campo in bitacora.columnas),
        filas=filas,
    )
    await _auditar_exportacion(tenant, sesion, nombre, filtros, "pdf", len(filas))
    return contenido


# --- Nivel de la bitácora de excepciones --------------------------------------------


async def nivel_excepciones(tenant: Tenant) -> str:
    config = await repositorios.leer_configuracion(tenant.base_mongodb, "excepciones")
    return config["nivel"] if config else logging.getLevelName(excepciones.NIVEL_POR_DEFECTO)


async def cambiar_nivel_excepciones(tenant: Tenant, sesion: Sesion, nivel: str) -> str:
    if sesion.rol not in ROLES_CONFIGURAN_NIVEL:
        raise ErrorDominio("USR_PER_000", "auditoria.nivel")
    nivel = nivel.upper()
    if nivel not in excepciones.NIVELES:
        raise ErrorDominio("USR_VAL_002", "Nivel inválido.")
    anterior = await nivel_excepciones(tenant)
    await repositorios.guardar_configuracion(tenant.base_mongodb, "excepciones", {"nivel": nivel})
    excepciones.actualizar_nivel(tenant.slug, nivel)
    await repositorios.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="auditoria.nivel_modificado",
            usuario=sesion.email,
            tenant_id=str(tenant.id),
            detalle={"anterior": anterior, "nuevo": nivel},
        ),
    )
    return nivel
