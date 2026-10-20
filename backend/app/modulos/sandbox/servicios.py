"""Casos de uso del Sandbox: ejecutar (CU-000-002-A), promover a PROD (B) e historial (C)."""

import asyncio
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import pyodbc
from starlette.concurrency import run_in_threadpool

from app.core import cifrado
from app.core.errores import ErrorDominio
from app.datos import sqlserver
from app.identidad import reautenticacion
from app.identidad.entidades import ROLES_OPERATIVOS, Sesion
from app.modulos.alertas import notificaciones
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.chat_ia.analizador import analizar
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import Conexion
from app.modulos.contexto_negocio import servicios as contexto
from app.modulos.historico_schema import servicios as historico
from app.modulos.sandbox import cliente, repositorios
from app.tenants.entidades import Tenant
from app.web.i18n import traducir

log = logging.getLogger(__name__)

ROLES_PROMUEVEN = ROLES_OPERATIVOS
VIGENCIA_ESTRUCTURA = timedelta(hours=24)
LARGO_MAXIMO_SQL = 100_000
SEPARADOR_LOTES = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)
NS_PLAN = {"p": "http://schemas.microsoft.com/sqlserver/2004/07/showplan"}

_tareas: set[asyncio.Task] = set()


def clave_de(tenant: Tenant, conexion: Conexion) -> str:
    """Un Sandbox por servidor de origen y tenant (decisión del TFI: uno por IP)."""
    host = conexion.host.lower()
    legible = re.sub(r"[^a-z0-9]+", "-", f"{tenant.slug}-{host}").strip("-")[:52]
    return f"{legible}-{hashlib.sha256(host.encode()).hexdigest()[:6]}"


def lotes_de(sql: str) -> list[str]:
    """Separa por GO, como SSMS: cada lote se envía por separado."""
    return [lote.strip() for lote in SEPARADOR_LOTES.split(sql or "") if lote.strip()]


def _origen(conexion: Conexion) -> dict:
    return {
        "servidor": conexiones.servidor_de(conexion),
        "usuario": conexion.usuario_sql,
        "password": cifrado.descifrar(conexion.password_sql_cifrada),
    }


async def _auditar(tenant: Tenant, usuario: str, accion: str, detalle: dict) -> None:
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(accion=accion, usuario=usuario, tenant_id=str(tenant.id), detalle=detalle),
    )


def _fecha(valor: datetime | None) -> str | None:
    return valor.isoformat() + "Z" if valor else None


# --- Estado y sincronización --------------------------------------------------------


async def estado(tenant: Tenant, conexion_id: str) -> dict:
    conexion = await conexiones.obtener(tenant, conexion_id)
    clave = clave_de(tenant, conexion)
    fila = await run_in_threadpool(
        repositorios.obtener, tenant.base_sqlserver, str(tenant.id), clave
    )
    try:
        contenedor = await cliente.estado(clave)
    except ErrorDominio:
        contenedor = {"disponible": False}
    sincronizado = fila["sincronizado_en"] if fila else None
    return {
        "clave": clave,
        "estado": fila["estado"] if fila else "sin_crear",
        "sincronizado_en": _fecha(sincronizado),
        "desactualizado": sincronizado is None
        or datetime.now(UTC).replace(tzinfo=None) - sincronizado > VIGENCIA_ESTRUCTURA,
        "bases": fila["bases"] if fila else [],
        "error": fila["error"] if fila else None,
        "contenedor": contenedor,
        "sincronizacion_diaria": (
            f"{conexion.sandbox_sincronizacion:%H:%M} ({conexion.zona_horaria})"
        ),
    }


def _bases_de_usuario(conexion: Conexion) -> list[str]:
    try:
        with sqlserver.conectar_externo(
            conexiones.servidor_de(conexion),
            conexion.usuario_sql,
            cifrado.descifrar(conexion.password_sql_cifrada),
        ) as abierta:
            filas = abierta.cursor().execute(
                """SELECT name FROM sys.databases
                   WHERE database_id > 4 AND state_desc = 'ONLINE' ORDER BY name"""
            )
            return [f[0] for f in filas.fetchall()]
    except pyodbc.Error as error:
        raise ErrorDominio("CON_DB_000", str(error)) from error


async def sincronizar(tenant: Tenant, actor: Sesion | None, conexion_id: str) -> None:
    """Lanza la sincronización en segundo plano (puede llevar varios minutos)."""
    conexion = await conexiones.obtener(tenant, conexion_id)
    clave = clave_de(tenant, conexion)
    actual = await run_in_threadpool(
        repositorios.obtener, tenant.base_sqlserver, str(tenant.id), clave
    )
    if actual and actual["estado"] == "sincronizando":
        return
    await run_in_threadpool(
        repositorios.guardar,
        tenant.base_sqlserver,
        str(tenant.id),
        clave,
        conexion.host,
        "sincronizando",
    )
    tarea = asyncio.create_task(_sincronizar(tenant, actor, conexion, clave))
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)


async def _sincronizar(
    tenant: Tenant, actor: Sesion | None, conexion: Conexion, clave: str
) -> None:
    usuario = actor.email if actor else "sistema (sincronización diaria)"
    try:
        bases = await run_in_threadpool(_bases_de_usuario, conexion)
        resultados = await cliente.sincronizar(clave, _origen(conexion), bases)
        correctas = [r for r in resultados if r["estado"] == "ok"]
        estado_final = "listo" if correctas or not resultados else "error"
        error = "; ".join(f"{r['base']}: {r['error']}" for r in resultados if r["error"])[:1000]
        await run_in_threadpool(
            repositorios.guardar,
            tenant.base_sqlserver,
            str(tenant.id),
            clave,
            conexion.host,
            estado_final,
            resultados,
            error or None,
            True,
        )
    except Exception as falla:
        log.warning("SBX_DCK_000 sincronización de %s: %s", clave, falla)
        await run_in_threadpool(
            repositorios.guardar,
            tenant.base_sqlserver,
            str(tenant.id),
            clave,
            conexion.host,
            "error",
            None,
            str(falla)[:1000],
        )
        resultados = []
    await _auditar(
        tenant,
        usuario,
        "sandbox.sincronizado",
        {"conexion": conexion.alias, "clave": clave, "bases": [r["base"] for r in resultados]},
    )


# --- Ejecución (CU-000-002-A) -------------------------------------------------------


async def ejecutar(
    tenant: Tenant, actor: Sesion, conexion_id: str, base: str, sql: str, solo_sintaxis: bool
) -> dict:
    conexion = await conexiones.obtener(tenant, conexion_id)
    lotes = lotes_de(sql)
    if not lotes or len(sql) > LARGO_MAXIMO_SQL or not (base or "").strip():
        raise ErrorDominio("USR_VAL_002", "Elegí la base y escribí la consulta.")
    clave = clave_de(tenant, conexion)
    fila = await run_in_threadpool(
        repositorios.obtener, tenant.base_sqlserver, str(tenant.id), clave
    )
    sincronizadas = {b["base"] for b in (fila or {}).get("bases", []) if b["estado"] == "ok"}
    if base not in sincronizadas:
        raise ErrorDominio("SBX_SNP_001", base)

    avisos = []
    if datetime.now(UTC).replace(tzinfo=None) - fila["sincronizado_en"] > VIGENCIA_ESTRUCTURA:
        avisos.append("SBX_SNP_000")
    elif not solo_sintaxis:
        # Antes de cada validación se verifica que la estructura siga igual al origen.
        try:
            alineadas = await cliente.alineadas(clave, _origen(conexion), [base])
            if not alineadas.get(base):
                avisos.append("SBX_SNP_000")
        except ErrorDominio:
            avisos.append("SBX_SNP_000")

    resultado = await cliente.ejecutar(clave, base, lotes, solo_sintaxis)
    if solo_sintaxis:
        return {**resultado, "avisos": avisos}

    registro = {
        "tenant_id": str(tenant.id),
        "usuario": actor.email,
        "conexion_id": conexion.id,
        "conexion_alias": conexion.alias,
        "ambiente": conexion.ambiente,
        "base": base,
        "sql": sql,
        "estado": resultado["estado"],
        "error": resultado.get("error"),
        "duracion_ms": resultado.get("duracion_ms"),
        "filas": resultado.get("filas"),
        "plan": resultado.get("plan"),
        "plan_xml": resultado.get("plan_xml", [])[:3],
        "muestra": resultado.get("muestra"),
        "mensajes": resultado.get("mensajes", []),
        "estructura_del": fila["sincronizado_en"],
        "avisos": avisos,
        "promocion": None,
    }
    registro_id = await repositorios.registrar(tenant.base_mongodb, registro)
    return _registro_a_dict({**registro, "_id": registro_id, "ts": datetime.now(UTC)})


def _registro_a_dict(d: dict) -> dict:
    salida = {k: v for k, v in d.items() if k not in ("_id", "tenant_id")}
    salida["id"] = str(d["_id"])
    for campo in ("ts", "estructura_del"):
        valor = d.get(campo)
        if isinstance(valor, datetime):
            salida[campo] = (valor if valor.tzinfo else valor.replace(tzinfo=UTC)).isoformat()
    if d.get("promocion") and isinstance(d["promocion"].get("en"), datetime):
        salida["promocion"] = {
            **d["promocion"],
            "en": d["promocion"]["en"].replace(tzinfo=UTC).isoformat(),
        }
    return salida


# --- Historial (CU-000-002-C) -------------------------------------------------------


async def historial(
    tenant: Tenant, conexion_id: str | None, usuario: str | None, estado_filtro: str | None
) -> list[dict]:
    registros = await repositorios.listar(
        tenant.base_mongodb,
        str(tenant.id),
        {"conexion_id": conexion_id, "usuario": usuario, "estado": estado_filtro},
    )
    return [_registro_a_dict(r) for r in registros]


async def detalle(tenant: Tenant, registro_id: str) -> dict:
    registro = await repositorios.obtener_registro(tenant.base_mongodb, str(tenant.id), registro_id)
    if registro is None:
        raise ErrorDominio("SBX_PRM_000", registro_id)
    return _registro_a_dict(registro)


# --- Promoción a PROD (CU-000-002-B) ------------------------------------------------


def _resumen_estimado(planes: list[str]) -> dict:
    filas = costo = 0.0
    operador = None
    for xml in planes:
        try:
            raiz = ET.fromstring(xml)
        except ET.ParseError:
            continue
        for sentencia in raiz.iterfind(".//p:StmtSimple", NS_PLAN):
            filas += float(sentencia.get("StatementEstRows", 0) or 0)
            costo += float(sentencia.get("StatementSubTreeCost", 0) or 0)
            primero = sentencia.find(".//p:RelOp", NS_PLAN)
            if operador is None and primero is not None:
                operador = primero.get("PhysicalOp")
    return {"filas_estimadas": round(filas), "costo": round(costo, 4), "operador": operador}


def _abrir_real(conexion: Conexion, base: str):
    return sqlserver.abrir_externo(
        conexiones.servidor_de(conexion),
        conexion.usuario_sql,
        cifrado.descifrar(conexion.password_sql_cifrada),
        base,
    )


def _plan_estimado(conexion: Conexion, base: str, lotes: list[str]) -> dict:
    """Plan estimado sobre la base real, sin ejecutar nada (SHOWPLAN_XML)."""
    abierta = _abrir_real(conexion, base)
    try:
        cursor = abierta.cursor()
        cursor.execute("SET SHOWPLAN_XML ON")
        planes = []
        for lote in lotes:
            cursor.execute(lote)
            while True:
                if cursor.description:
                    planes += [f[0] for f in cursor.fetchall()]
                if not cursor.nextset():
                    break
        cursor.execute("SET SHOWPLAN_XML OFF")
        return _resumen_estimado(planes)
    except pyodbc.Error as error:
        raise ErrorDominio("SQL_EXE_000", str(error)) from error
    finally:
        abierta.close()


def _ejecutar_real(conexion: Conexion, base: str, lotes: list[str]) -> int:
    """Ejecuta en la base real dentro de una transacción; ante una falla, revierte."""
    abierta = _abrir_real(conexion, base)
    abierta.autocommit = False
    filas = 0
    try:
        cursor = abierta.cursor()
        for lote in lotes:
            cursor.execute(lote)
            while True:
                if not cursor.description and cursor.rowcount and cursor.rowcount > 0:
                    filas += cursor.rowcount
                if not cursor.nextset():
                    break
        abierta.commit()
        return filas
    except pyodbc.Error as error:
        abierta.rollback()
        raise ErrorDominio("SQL_EXE_000", str(error)) from error
    finally:
        abierta.close()


async def _promovible(tenant: Tenant, actor: Sesion, registro_id: str) -> tuple[dict, Conexion]:
    if actor.rol not in ROLES_PROMUEVEN:
        raise ErrorDominio("USR_PER_000", "sandbox.promover")
    registro = await repositorios.obtener_registro(tenant.base_mongodb, str(tenant.id), registro_id)
    if registro is None or registro["estado"] != "ok" or registro.get("promocion"):
        raise ErrorDominio("SBX_PRM_000", registro_id)
    return registro, await conexiones.obtener(tenant, registro["conexion_id"])


async def previa(tenant: Tenant, actor: Sesion, registro_id: str) -> dict:
    """Resumen comparativo Sandbox vs. instancia real antes de promover."""
    registro, conexion = await _promovible(tenant, actor, registro_id)
    estimado = await run_in_threadpool(
        _plan_estimado, conexion, registro["base"], lotes_de(registro["sql"])
    )
    analisis = analizar(registro["sql"], registro["base"])
    bloqueos, advertencias = await contexto.evaluar_reglas(
        tenant, conexion, registro["base"], analisis
    )
    return {
        "sandbox": {
            "filas": registro["filas"],
            "duracion_ms": registro["duracion_ms"],
            "operador": (registro.get("plan") or {}).get("operador"),
            "costo": (registro.get("plan") or {}).get("costo"),
        },
        "real": estimado,
        "conexion": conexion.alias,
        "ambiente": conexion.ambiente,
        "base": registro["base"],
        "reglas_bloqueo": bloqueos,
        "advertencias": advertencias,
        "segundo_factor": conexion.ambiente == "PROD" or analisis.destructiva,
    }


async def promover(tenant: Tenant, actor: Sesion, registro_id: str) -> dict:
    registro, conexion = await _promovible(tenant, actor, registro_id)
    analisis = analizar(registro["sql"], registro["base"])
    bloqueos, _ = await contexto.evaluar_reglas(tenant, conexion, registro["base"], analisis)
    if bloqueos:
        raise ErrorDominio("CTX_REG_000", None, {"reglas": bloqueos})
    # PROD y las sentencias destructivas (DELETE, TRUNCATE, DROP) piden segundo factor.
    if conexion.ambiente == "PROD" or analisis.destructiva:
        reautenticacion.exigir_segundo_factor_reciente(actor)
    lotes = lotes_de(registro["sql"])
    # Histórico de schema: snapshot previo si la sentencia cambia estructura.
    previa = await historico.preparar(conexion, registro["base"], registro["sql"])
    try:
        filas = await run_in_threadpool(_ejecutar_real, conexion, registro["base"], lotes)
        estado_promocion, error = "exitosa", None
        # Quien escribió y validó la sentencia y quien la autorizó al promoverla.
        await historico.registrar(
            tenant,
            previa,
            registro["sql"],
            registro["usuario"],
            "promocion",
            registro_id,
            autorizado_por=actor.email,
        )
    except ErrorDominio as falla:
        filas, estado_promocion, error = 0, "fallida", falla.detalle
    promocion = {
        "por": actor.email,
        "en": datetime.now(UTC),
        "estado": estado_promocion,
        "filas": filas,
        "error": error,
    }
    if estado_promocion == "exitosa":
        await repositorios.marcar_promovida(tenant.base_mongodb, registro_id, promocion)
    await auditoria.registrar_transaccion(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="sandbox.promovido",
            usuario=actor.email,
            tenant_id=str(tenant.id),
            detalle={
                "promocion": True,
                "registro_sandbox": registro_id,
                "conexion": conexion.alias,
                "ambiente": conexion.ambiente,
                "base": registro["base"],
                "sql": registro["sql"],
                "estado": estado_promocion,
                "filas": filas,
                "error": error,
            },
        ),
    )
    # Aviso interno al equipo DBA (CU-000-002-B, paso 6).
    await notificaciones.avisar(
        tenant,
        ROLES_OPERATIVOS,
        traducir("sandbox.aviso.titulo").format(conexion=conexion.alias),
        traducir(f"sandbox.aviso.{estado_promocion}").format(
            usuario=actor.email, base=registro["base"], filas=filas, error=error or ""
        ),
        f"/sandbox?registro={registro_id}",
    )
    if estado_promocion == "fallida":
        raise ErrorDominio("SQL_EXE_000", error)
    return {"estado": estado_promocion, "filas": filas}
