"""Casos de uso de Buenas Prácticas (CU-000-007 A y B).

El análisis corre en segundo plano porque combina varias lecturas sobre la instancia con
una consulta al motor IA: la pantalla consulta el estado del informe hasta que termina.
Aplicar una sugerencia siempre pasa por el Sandbox; la promoción a la base real es la del
módulo Sandbox (CU-000-002-B).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pyodbc
from starlette.concurrency import run_in_threadpool

from app.core import cifrado
from app.core.errores import ErrorDominio
from app.datos import sqlserver
from app.identidad import repositorios as usuarios
from app.identidad.entidades import Sesion
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.buenas_practicas import analisis_ia, chequeos, repositorios
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import Conexion
from app.modulos.contexto_negocio import esquema
from app.modulos.contexto_negocio import servicios as contexto
from app.modulos.sandbox import servicios as sandbox
from app.tenants.entidades import Tenant

log = logging.getLogger(__name__)

ROLES_ANALIZAN = frozenset({"ADMINISTRADOR", "DBA"})
# CU-000-007-B y la definición de reglas son del DBA Senior (rol DBA).
ROLES_APLICAN = frozenset({"DBA"})
VENCIMIENTO_EN_CURSO = timedelta(minutes=15)
LARGO_MAXIMO_REGLA = 500

_tareas: set[asyncio.Task] = set()


def permisos(sesion: Sesion) -> dict:
    return {
        "analizar": sesion.rol in ROLES_ANALIZAN,
        "aplicar": sesion.rol in ROLES_APLICAN,
        "reglas": sesion.rol in ROLES_APLICAN,
    }


def _exigir(sesion: Sesion, roles: frozenset[str], accion: str) -> None:
    if sesion.rol not in roles:
        raise ErrorDominio("USR_PER_000", accion)


async def _auditar(tenant: Tenant, usuario: str, accion: str, detalle: dict) -> None:
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(accion=accion, usuario=usuario, tenant_id=str(tenant.id), detalle=detalle),
    )


async def _conexion_habilitada(tenant: Tenant, sesion: Sesion, conexion_id: str) -> Conexion:
    conexion = await conexiones.obtener(tenant, conexion_id)
    usuario = await run_in_threadpool(
        usuarios.obtener, tenant.base_sqlserver, str(tenant.id), sesion.usuario_id
    )
    if usuario is None or conexion.ambiente not in usuario.ambientes:
        raise ErrorDominio("USR_PER_000", f"ambiente {conexion.ambiente}")
    return conexion


async def conexiones_disponibles(tenant: Tenant, sesion: Sesion) -> list[dict]:
    usuario = await run_in_threadpool(
        usuarios.obtener, tenant.base_sqlserver, str(tenant.id), sesion.usuario_id
    )
    ambientes = usuario.ambientes if usuario else ()
    return [
        {"id": c.id, "alias": c.alias, "ambiente": c.ambiente}
        for c in await conexiones.listar(tenant)
        if c.ambiente in ambientes
    ]


async def bases(tenant: Tenant, sesion: Sesion, conexion_id: str) -> list[str]:
    conexion = await _conexion_habilitada(tenant, sesion, conexion_id)
    return sorted(await esquema.obtener(tenant, conexion.id), key=str.lower)


def _fecha(valor: datetime | None) -> str | None:
    if valor is None:
        return None
    return (valor if valor.tzinfo else valor.replace(tzinfo=UTC)).isoformat()


def _informe(d: dict, completo: bool = False) -> dict:
    estado = d["estado"]
    ts = d["ts"] if d["ts"].tzinfo else d["ts"].replace(tzinfo=UTC)
    # Un análisis que quedó a medias (por ejemplo, por un reinicio) no sigue "en curso".
    if estado == "en_curso" and datetime.now(UTC) - ts > VENCIMIENTO_EN_CURSO:
        estado = "interrumpido"
    salida = {
        "id": str(d["_id"]),
        "ts": _fecha(d["ts"]),
        "conexion_id": d["conexion_id"],
        "conexion_alias": d["conexion_alias"],
        "ambiente": d["ambiente"],
        "base": d["base"],
        "tipos": d["tipos"],
        "usuario": d["usuario"],
        "estado": estado,
        "error": d.get("error"),
        "resumen": d.get("resumen"),
        "conteo": _conteo(d.get("sugerencias") or []),
    }
    if completo:
        salida["sugerencias"] = d.get("sugerencias") or []
        salida["hallazgos"] = d.get("hallazgos") or []
    return salida


def _conteo(sugerencias: list[dict]) -> dict:
    conteo = {"alta": 0, "media": 0, "baja": 0}
    for s in sugerencias:
        conteo[s["severidad"]] = conteo.get(s["severidad"], 0) + 1
    return conteo


# --- CU-000-007-A: solicitar análisis -----------------------------------------------


async def solicitar(
    tenant: Tenant, sesion: Sesion, conexion_id: str, base: str, tipos: list[str]
) -> dict:
    _exigir(sesion, ROLES_ANALIZAN, "buenas_practicas.analizar")
    tipos = [t for t in chequeos.TIPOS if t in tipos]
    if not tipos:
        raise ErrorDominio("USR_VAL_002", "Elegí al menos un tipo de análisis.")
    conexion = await _conexion_habilitada(tenant, sesion, conexion_id)
    bases = await esquema.obtener(tenant, conexion.id)
    nombre = next((b for b in bases if b.lower() == (base or "").lower()), None)
    if nombre is None:
        raise ErrorDominio("USR_VAL_002", f"Base inexistente o sin acceso: {base}")

    informe_id = await repositorios.crear_informe(
        tenant.base_mongodb,
        {
            "tenant_id": str(tenant.id),
            "conexion_id": conexion.id,
            "conexion_alias": conexion.alias,
            "ambiente": conexion.ambiente,
            "base": nombre,
            "tipos": tipos,
            "usuario": sesion.email,
            "estado": "en_curso",
        },
    )
    tarea = asyncio.create_task(
        _analizar(tenant, sesion.email, conexion, nombre, tipos, informe_id)
    )
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)
    return await informe(tenant, sesion, informe_id)


def _leer(conexion: Conexion, base: str, tipos: list[str]) -> list[dict]:
    try:
        with sqlserver.conectar_externo(
            conexiones.servidor_de(conexion),
            conexion.usuario_sql,
            cifrado.descifrar(conexion.password_sql_cifrada),
            base,
            timeout=15,
        ) as abierta:
            return chequeos.ejecutar(abierta, tipos)
    except pyodbc.Error as error:
        raise ErrorDominio("MNT_DAT_000", str(error)) from error


async def _analizar(
    tenant: Tenant, usuario: str, conexion: Conexion, base: str, tipos: list[str], informe_id: str
) -> None:
    campos: dict = {}
    try:
        hallazgos = await run_in_threadpool(_leer, conexion, base, tipos)
        campos["hallazgos"] = hallazgos
        reglas = [
            r["texto"]
            for r in await repositorios.listar_reglas(tenant.base_mongodb, str(tenant.id))
            if r.get("activa", True)
        ]
        resultado = await analisis_ia.analizar(
            await contexto.texto_para_ia(tenant, conexion), base, hallazgos, reglas
        )
        campos |= {
            "estado": "listo",
            "resumen": resultado.resumen,
            "sugerencias": [
                {**s.model_dump(), "id": n, "sandbox": None}
                for n, s in enumerate(resultado.sugerencias)
            ],
        }
    except ErrorDominio as error:
        log.warning(
            "%s análisis de buenas prácticas %s: %s", error.codigo, informe_id, error.detalle
        )
        campos |= {"estado": "error", "error": error.codigo}
    except Exception:
        log.exception("Falló el análisis de buenas prácticas %s", informe_id)
        campos |= {"estado": "error", "error": "MNT_DAT_000"}
    await repositorios.actualizar_informe(tenant.base_mongodb, informe_id, campos)
    await _auditar(
        tenant,
        usuario,
        "buenas_practicas.analizado",
        {
            "informe": informe_id,
            "conexion": conexion.alias,
            "base": base,
            "tipos": tipos,
            "estado": campos["estado"],
            "sugerencias": len(campos.get("sugerencias", [])),
        },
    )


# --- Consulta de informes -----------------------------------------------------------


async def informes(tenant: Tenant, sesion: Sesion, conexion_id: str | None) -> list[dict]:
    _exigir(sesion, ROLES_ANALIZAN, "buenas_practicas.ver")
    filas = await repositorios.listar_informes(tenant.base_mongodb, str(tenant.id), conexion_id)
    return [_informe(d) for d in filas]


async def _obtener(tenant: Tenant, informe_id: str) -> dict:
    documento = await repositorios.obtener_informe(tenant.base_mongodb, str(tenant.id), informe_id)
    if documento is None:
        raise ErrorDominio("BPR_VAL_000", informe_id)
    return documento


async def informe(tenant: Tenant, sesion: Sesion, informe_id: str) -> dict:
    _exigir(sesion, ROLES_ANALIZAN, "buenas_practicas.ver")
    return _informe(await _obtener(tenant, informe_id), completo=True)


# --- CU-000-007-B: aplicar una sugerencia (pasa por el Sandbox) ---------------------


async def validar_en_sandbox(
    tenant: Tenant, sesion: Sesion, informe_id: str, sugerencia_id: int
) -> dict:
    _exigir(sesion, ROLES_APLICAN, "buenas_practicas.aplicar")
    documento = await _obtener(tenant, informe_id)
    sugerencias = documento.get("sugerencias") or []
    sugerencia = next((s for s in sugerencias if s["id"] == sugerencia_id), None)
    if sugerencia is None:
        raise ErrorDominio("BPR_VAL_000", f"{informe_id}/{sugerencia_id}")
    if not (sugerencia.get("sql") or "").strip():
        raise ErrorDominio("BPR_VAL_001")
    conexion = await _conexion_habilitada(tenant, sesion, documento["conexion_id"])
    resultado = await sandbox.ejecutar(
        tenant, sesion, conexion.id, documento["base"], sugerencia["sql"], False
    )
    sugerencia["sandbox"] = {
        "registro_id": resultado["id"],
        "estado": resultado["estado"],
        "error": resultado.get("error"),
        "avisos": resultado.get("avisos", []),
    }
    await repositorios.actualizar_informe(
        tenant.base_mongodb, informe_id, {"sugerencias": sugerencias}
    )
    await _auditar(
        tenant,
        sesion.email,
        "buenas_practicas.validada",
        {
            "informe": informe_id,
            "sugerencia": sugerencia["titulo"],
            "registro_sandbox": resultado["id"],
            "estado": resultado["estado"],
        },
    )
    return sugerencia


# --- Reglas propias del equipo ------------------------------------------------------


def _regla(d: dict) -> dict:
    return {"id": str(d["_id"]), "texto": d["texto"], "activa": d.get("activa", True)}


async def reglas(tenant: Tenant) -> list[dict]:
    return [
        _regla(d) for d in await repositorios.listar_reglas(tenant.base_mongodb, str(tenant.id))
    ]


async def guardar_regla(
    tenant: Tenant, sesion: Sesion, regla_id: str | None, texto: str, activa: bool
) -> dict:
    _exigir(sesion, ROLES_APLICAN, "buenas_practicas.reglas")
    texto = (texto or "").strip()
    if not texto or len(texto) > LARGO_MAXIMO_REGLA:
        raise ErrorDominio("USR_VAL_002", "Escribí la regla (hasta 500 caracteres).")
    nuevo_id = await repositorios.guardar_regla(
        tenant.base_mongodb, str(tenant.id), regla_id, {"texto": texto, "activa": activa}
    )
    if nuevo_id is None:
        raise ErrorDominio("BPR_VAL_000", regla_id)
    await _auditar(
        tenant,
        sesion.email,
        f"buenas_practicas.regla_{'modificada' if regla_id else 'creada'}",
        {"id": nuevo_id, "texto": texto, "activa": activa},
    )
    return {"id": nuevo_id, "texto": texto, "activa": activa}


async def eliminar_regla(tenant: Tenant, sesion: Sesion, regla_id: str) -> None:
    _exigir(sesion, ROLES_APLICAN, "buenas_practicas.reglas")
    if not await repositorios.eliminar_regla(tenant.base_mongodb, str(tenant.id), regla_id):
        raise ErrorDominio("BPR_VAL_000", regla_id)
    await _auditar(tenant, sesion.email, "buenas_practicas.regla_eliminada", {"id": regla_id})
