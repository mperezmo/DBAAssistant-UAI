"""Casos de uso del Chat IA (CU-000-001 A, B y C).

Política de ejecución (definida con el usuario):
- SELECT: directo, sin segundo factor; se muestran hasta ``chat_filas_pantalla`` filas y
  el total; el CSV exporta todas.
- INSERT, UPDATE y DELETE: directo, con WHERE obligatorio (en INSERT…SELECT, en su
  SELECT), sin bloqueos ni transacciones abiertas sobre las tablas y en una transacción.
  Piden segundo factor en PROD, en DELETE y en tablas sensibles con doble confirmación.
- Todo lo demás (DDL, MERGE, EXEC, varias sentencias): obligatoriamente por el Sandbox.
- Consulta solo ejecuta SELECT. Siempre en conexiones de ambientes habilitados al usuario.
"""

import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import reautenticacion
from app.identidad import repositorios as usuarios
from app.identidad.entidades import Sesion
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.chat_ia import ejecucion, motor_ia, repositorios
from app.modulos.chat_ia.analizador import Analisis, analizar
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import Conexion
from app.modulos.contexto_negocio import esquema
from app.modulos.contexto_negocio import servicios as contexto
from app.modulos.sandbox import servicios as sandbox
from app.tenants.entidades import Tenant

log = logging.getLogger(__name__)

TURNOS_DE_HISTORIAL = 6
LARGO_MAXIMO_PEDIDO = 2000
BASES_DE_SISTEMA = ("master",)


async def _auditar_transaccion(tenant: Tenant, actor: Sesion, accion: str, detalle: dict) -> None:
    await auditoria.registrar_transaccion(
        tenant.base_mongodb,
        EventoAuditoria(
            accion=accion, usuario=actor.email, tenant_id=str(tenant.id), detalle=detalle
        ),
    )


async def _ambientes_del_usuario(tenant: Tenant, sesion: Sesion) -> tuple[str, ...]:
    usuario = await run_in_threadpool(
        usuarios.obtener, tenant.base_sqlserver, str(tenant.id), sesion.usuario_id
    )
    return usuario.ambientes if usuario else ()


async def conexiones_disponibles(tenant: Tenant, sesion: Sesion) -> list[dict]:
    ambientes = await _ambientes_del_usuario(tenant, sesion)
    return [
        {"id": c.id, "alias": c.alias, "ambiente": c.ambiente}
        for c in await conexiones.listar(tenant)
        if c.ambiente in ambientes
    ]


async def _conexion_habilitada(tenant: Tenant, sesion: Sesion, conexion_id: str) -> Conexion:
    conexion = await conexiones.obtener(tenant, conexion_id)
    if conexion.ambiente not in await _ambientes_del_usuario(tenant, sesion):
        raise ErrorDominio("USR_PER_000", f"ambiente {conexion.ambiente}")
    return conexion


# --- Hilos --------------------------------------------------------------------------


async def hilos(tenant: Tenant, sesion: Sesion) -> list[dict]:
    return await repositorios.listar(tenant.base_mongodb, str(tenant.id), sesion.usuario_id)


async def hilo(tenant: Tenant, sesion: Sesion, hilo_id: str) -> dict:
    encontrado = await repositorios.obtener(
        tenant.base_mongodb, str(tenant.id), sesion.usuario_id, hilo_id
    )
    if encontrado is None:
        raise ErrorDominio("CHT_VAL_000", hilo_id)
    return encontrado


async def eliminar_hilo(tenant: Tenant, sesion: Sesion, hilo_id: str) -> None:
    if not await repositorios.eliminar(
        tenant.base_mongodb, str(tenant.id), sesion.usuario_id, hilo_id
    ):
        raise ErrorDominio("CHT_VAL_000", hilo_id)


def _mensaje(hilo_actual: dict, mensaje_id: str) -> dict:
    for mensaje in hilo_actual["mensajes"]:
        if mensaje["id"] == mensaje_id and mensaje["rol"] == "asistente":
            return mensaje
    raise ErrorDominio("CHT_VAL_000", mensaje_id)


def _historial(hilo_actual: dict) -> list[dict]:
    """Últimos turnos del hilo para que el motor IA entienda preguntas de seguimiento."""
    turnos = []
    for mensaje in hilo_actual["mensajes"][-TURNOS_DE_HISTORIAL * 2 :]:
        if mensaje["rol"] == "usuario":
            turnos.append({"role": "user", "content": mensaje["texto"]})
        else:
            resumen = (
                f"Base: {mensaje.get('base') or '-'}\nSQL: {mensaje.get('sql') or '-'}\n"
                f"{mensaje.get('explicacion', '')}"
            )
            turnos.append({"role": "assistant", "content": resumen})
    # La API exige que el primer turno sea del usuario.
    while turnos and turnos[0]["role"] != "user":
        turnos.pop(0)
    return turnos


# --- Revisión previa: qué se puede hacer con un T-SQL -------------------------------


async def revisar(tenant: Tenant, sesion: Sesion, conexion: Conexion, base: str, sql: str) -> dict:
    """Lo que la pantalla necesita para ofrecer Ejecutar o Validar en Sandbox."""
    analisis = analizar(sql, base)
    sensibles = await contexto.sensibles_de(tenant, conexion.id, analisis.tablas)
    bloqueos, advertencias = await contexto.evaluar_reglas(tenant, conexion, base, analisis)
    motivo = None
    if analisis.tipo not in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        motivo = "sandbox"
    elif sesion.rol == "CONSULTA" and not analisis.es_lectura:
        motivo = "solo_lectura"
    elif analisis.falta_where:
        motivo = "falta_where"
    elif any(sesion.rol not in s["roles_consulta"] for s in sensibles):
        motivo = "tabla_restringida"
    elif bloqueos:
        motivo = "regla"
    return {
        "tipo": analisis.tipo,
        "directo": motivo is None,
        "motivo": motivo,
        "segundo_factor": analisis.es_escritura
        and _pide_segundo_factor(conexion, analisis, sensibles),
        "sensibles": [f"{s['base']}.{s['tabla']}" for s in sensibles],
        "reglas_bloqueo": bloqueos,
        "advertencias": advertencias,
        "puede_sandbox": sesion.puede_ver("sandbox"),
        "destructiva": analisis.destructiva,
    }


def _pide_segundo_factor(conexion: Conexion, analisis: Analisis, sensibles: list[dict]) -> bool:
    return (
        conexion.ambiente == "PROD"
        or analisis.tipo == "DELETE"
        or any(s["doble_confirmacion"] for s in sensibles)
    )


async def _base_valida(tenant: Tenant, conexion: Conexion, base: str | None) -> str:
    base = (base or "").strip()
    bases = await esquema.obtener(tenant, conexion.id)
    for nombre in [*bases, *BASES_DE_SISTEMA]:
        if nombre.lower() == base.lower():
            return nombre
    raise ErrorDominio("USR_VAL_002", f"Base inexistente o sin acceso: {base}")


# --- CU-000-001-A: consulta en lenguaje natural -------------------------------------


async def enviar(
    tenant: Tenant, sesion: Sesion, hilo_id: str | None, conexion_id: str | None, texto: str
) -> dict:
    texto = (texto or "").strip()[:LARGO_MAXIMO_PEDIDO]
    if not texto:
        raise ErrorDominio("USR_VAL_002", "Escribí el pedido.")
    if hilo_id:
        actual = await hilo(tenant, sesion, hilo_id)
        conexion_id = actual["conexion_id"]
    elif not conexion_id:
        raise ErrorDominio("USR_VAL_002", "Elegí la instancia.")
    conexion = await _conexion_habilitada(tenant, sesion, conexion_id)
    if not hilo_id:
        hilo_id = await repositorios.crear(
            tenant.base_mongodb, str(tenant.id), sesion.usuario_id, conexion.id, texto[:80]
        )
        actual = await hilo(tenant, sesion, hilo_id)

    inicio = time.monotonic()
    traduccion = await motor_ia.traducir(
        await contexto.texto_para_ia(tenant, conexion), _historial(actual), texto
    )
    respuesta = {
        "id": uuid4().hex,
        "rol": "asistente",
        "ts": datetime.now(UTC).isoformat(),
        "sql": (traduccion.sql or "").strip() or None,
        "base": traduccion.base,
        "explicacion": traduccion.explicacion,
        "confianza": traduccion.confianza,
        "duracion_ms": round((time.monotonic() - inicio) * 1000),
        "revision": None,
        "ejecucion": None,
        "sandbox": None,
    }
    if respuesta["sql"]:
        try:
            respuesta["base"] = await _base_valida(tenant, conexion, traduccion.base)
            respuesta["revision"] = await revisar(
                tenant, sesion, conexion, respuesta["base"], respuesta["sql"]
            )
        except ErrorDominio as error:
            respuesta["revision"] = {"error": error.definicion.mensaje}
    pedido = {
        "id": uuid4().hex,
        "rol": "usuario",
        "texto": texto,
        "ts": datetime.now(UTC).isoformat(),
    }
    await repositorios.agregar_mensajes(tenant.base_mongodb, hilo_id, [pedido, respuesta])
    return {"hilo_id": hilo_id, "pedido": pedido, "respuesta": respuesta}


async def revisar_edicion(
    tenant: Tenant, sesion: Sesion, hilo_id: str, base: str, sql: str
) -> dict:
    """Revisión de un T-SQL editado a mano antes de ejecutarlo."""
    actual = await hilo(tenant, sesion, hilo_id)
    conexion = await _conexion_habilitada(tenant, sesion, actual["conexion_id"])
    return await revisar(tenant, sesion, conexion, await _base_valida(tenant, conexion, base), sql)


async def _validar_ejecucion(
    tenant: Tenant,
    sesion: Sesion,
    conexion: Conexion,
    base: str,
    sql: str,
    confirmar_advertencias: bool,
) -> tuple[Analisis, list[dict]]:
    """Verificaciones del flujo normal (paso 7) y alternativos del CU-000-001-A."""
    analisis = analizar(sql, base)
    if analisis.tipo not in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        raise ErrorDominio("SQL_VAL_001", analisis.motivo)
    if sesion.rol == "CONSULTA" and not analisis.es_lectura:
        raise ErrorDominio("USR_PER_000", "Consulta solo ejecuta SELECT")
    if analisis.falta_where:
        raise ErrorDominio("SQL_VAL_002")
    sensibles = await contexto.sensibles_de(tenant, conexion.id, analisis.tablas)
    contexto.verificar_acceso(sesion, sensibles)
    bloqueos, advertencias = await contexto.evaluar_reglas(tenant, conexion, base, analisis)
    if bloqueos:
        raise ErrorDominio("CTX_REG_000", None, {"reglas": bloqueos})
    if advertencias and not confirmar_advertencias:
        raise ErrorDominio("CTX_REG_001", None, {"reglas": advertencias})
    if analisis.es_escritura:
        if conexion.ambiente == "PROD" or analisis.tipo == "DELETE":
            reautenticacion.exigir_segundo_factor_reciente(sesion)
        if any(s["doble_confirmacion"] for s in sensibles) and not (
            reautenticacion.segundo_factor_vigente(sesion)
        ):
            raise ErrorDominio("CTX_VAL_001")
    return analisis, sensibles


async def ejecutar(
    tenant: Tenant,
    sesion: Sesion,
    hilo_id: str,
    mensaje_id: str,
    sql: str,
    base: str,
    confirmar_advertencias: bool = False,
) -> dict:
    actual = await hilo(tenant, sesion, hilo_id)
    mensaje = _mensaje(actual, mensaje_id)
    conexion = await _conexion_habilitada(tenant, sesion, actual["conexion_id"])
    base = await _base_valida(tenant, conexion, base)
    sql = (sql or "").strip()
    analisis, sensibles = await _validar_ejecucion(
        tenant, sesion, conexion, base, sql, confirmar_advertencias
    )
    detalle = {
        "conexion": conexion.alias,
        "ambiente": conexion.ambiente,
        "base": base,
        "sql": sql,
        "tipo": analisis.tipo,
        "editada": sql != mensaje.get("sql"),
        "hilo": hilo_id,
    }
    try:
        if analisis.es_lectura:
            resultado = await run_in_threadpool(
                ejecucion.leer,
                conexion,
                base,
                sql,
                obtener_configuracion().chat_filas_pantalla,
                contexto.columnas_enmascaradas(sesion, sensibles),
            )
            resumen = {"filas": len(resultado["filas"]), "total": resultado["total"]}
        else:
            ocupadas = await run_in_threadpool(ejecucion.bloqueos, conexion, base, analisis.tablas)
            if ocupadas:
                raise ErrorDominio("SQL_LCK_000", None, {"sesiones": ocupadas})
            resultado = await run_in_threadpool(ejecucion.escribir, conexion, base, sql)
            resumen = {"filas_afectadas": resultado["filas_afectadas"]}
    except ErrorDominio as error:
        if error.codigo == "SQL_EXE_000":
            await _auditar_transaccion(
                tenant,
                sesion,
                "chat.consulta_ejecutada",
                {**detalle, "estado": "fallida", "error": error.detalle},
            )
        raise
    registro = {
        "estado": "exitosa",
        "tipo": analisis.tipo,
        "duracion_ms": resultado["duracion_ms"],
        "ts": datetime.now(UTC).isoformat(),
        **resumen,
    }
    await repositorios.actualizar_mensaje(
        tenant.base_mongodb, hilo_id, mensaje_id, {"sql": sql, "base": base, "ejecucion": registro}
    )
    await _auditar_transaccion(tenant, sesion, "chat.consulta_ejecutada", {**detalle, **registro})
    return {**resultado, "tipo": analisis.tipo, "ejecucion": registro}


# --- CU-000-001-C: exportar ---------------------------------------------------------


async def exportar(tenant: Tenant, sesion: Sesion, hilo_id: str, mensaje_id: str):
    """Valida igual que la ejecución y devuelve el iterador de filas para el CSV."""
    actual = await hilo(tenant, sesion, hilo_id)
    mensaje = _mensaje(actual, mensaje_id)
    if not (mensaje.get("ejecucion") or {}).get("tipo") == "SELECT":
        raise ErrorDominio("USR_VAL_002", "Solo se exportan consultas ejecutadas.")
    conexion = await _conexion_habilitada(tenant, sesion, actual["conexion_id"])
    base = await _base_valida(tenant, conexion, mensaje["base"])
    _, sensibles = await _validar_ejecucion(tenant, sesion, conexion, base, mensaje["sql"], True)
    filas = ejecucion.iterar(
        conexion, base, mensaje["sql"], contexto.columnas_enmascaradas(sesion, sensibles)
    )

    async def auditar(cantidad: int) -> None:
        await _auditar_transaccion(
            tenant,
            sesion,
            "chat.exportado",
            {
                "conexion": conexion.alias,
                "ambiente": conexion.ambiente,
                "base": base,
                "sql": mensaje["sql"],
                "filas": cantidad,
                "formato": "csv",
            },
        )

    return filas, auditar


# --- Derivación al Sandbox (flujo alternativo 8.1) ----------------------------------


async def validar_en_sandbox(
    tenant: Tenant, sesion: Sesion, hilo_id: str, mensaje_id: str, sql: str, base: str
) -> dict:
    if not sesion.puede_ver("sandbox"):
        raise ErrorDominio("USR_PER_000", "sandbox")
    actual = await hilo(tenant, sesion, hilo_id)
    _mensaje(actual, mensaje_id)
    conexion = await _conexion_habilitada(tenant, sesion, actual["conexion_id"])
    base = await _base_valida(tenant, conexion, base)
    resultado = await sandbox.ejecutar(tenant, sesion, conexion.id, base, sql, False)
    resumen = {
        "registro_id": resultado["id"],
        "estado": resultado["estado"],
        "error": resultado.get("error"),
        "filas": resultado.get("filas"),
        "duracion_ms": resultado.get("duracion_ms"),
        "avisos": resultado.get("avisos", []),
    }
    await repositorios.actualizar_mensaje(
        tenant.base_mongodb, hilo_id, mensaje_id, {"sql": sql, "base": base, "sandbox": resumen}
    )
    return resumen
