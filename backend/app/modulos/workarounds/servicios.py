"""Casos de uso de workarounds (CU-000-005-A a F)."""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pyodbc
from starlette.concurrency import run_in_threadpool

from app.core import cifrado
from app.core.errores import ErrorDominio
from app.datos import sqlserver, winrm
from app.identidad import reautenticacion
from app.identidad import repositorios as usuarios
from app.identidad.entidades import AMBIENTES, ROLES_OPERATIVOS, Sesion
from app.modulos.alertas import notificaciones
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import Conexion
from app.modulos.historico_schema import servicios as historico
from app.modulos.sandbox import servicios as sandbox
from app.modulos.workarounds import ejecutor, integrados, repositorios
from app.modulos.workarounds.entidades import (
    CATEGORIAS,
    CRITICIDADES,
    EVENTOS_AUTOMATIZABLES,
    ORIGENES,
    VIGENCIA_AUTORIZACION_HORAS,
    VIGENCIA_PEDIDO_HORAS,
    Automatizacion,
    Ejecucion,
    Paso,
    Propio,
)
from app.tenants.entidades import Tenant
from app.web.i18n import traducir

log = logging.getLogger(__name__)

ROLES_CREAN = ROLES_OPERATIVOS
ROLES_AUTORIZAN = ROLES_OPERATIVOS
ROLES_EJECUTORES = ROLES_OPERATIVOS
USUARIO_AUTOMATICO = "sistema (ejecución automática)"
MAXIMO_PASOS = 20
LARGO_PASO = 20_000

# Tareas de ejecución en curso: se guarda la referencia para que no se descarten.
_tareas: set[asyncio.Task] = set()


def _exigir(sesion: Sesion, roles, accion: str) -> None:
    if sesion.rol not in roles:
        raise ErrorDominio("USR_PER_000", accion)


async def _auditar(tenant: Tenant, usuario: str, accion: str, detalle: dict) -> None:
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(accion=accion, usuario=usuario, tenant_id=str(tenant.id), detalle=detalle),
    )


def permisos(sesion: Sesion) -> dict:
    return {
        "crear": sesion.rol in ROLES_CREAN,
        "autorizar": sesion.rol in ROLES_AUTORIZAN,
        "automatizar": sesion.rol in ROLES_CREAN,
    }


# --- Biblioteca (CU-000-005-D) ------------------------------------------------------


def _ficha_integrado(i: integrados.Integrado, stats: dict) -> dict:
    cantidad, ultima = stats.get(i.id, (0, None))
    return {
        "id": i.id,
        "codigo": i.codigo,
        "nombre": i.nombre,
        "descripcion": i.descripcion,
        "categoria": i.categoria,
        "criticidad": i.criticidad,
        "ambientes": list(i.ambientes),
        "roles": list(i.roles),
        "integrado": True,
        "estado": "integrado",
        "requiere_base": i.requiere_base,
        "requiere_ventana": i.requiere_ventana,
        "eventos_automaticos": list(i.eventos_automaticos),
        "transaccional": i.transaccional,
        "ejecuciones": cantidad,
        "ultima_ejecucion": ultima.isoformat() + "Z" if ultima else None,
    }


def _ficha_propio(w: Propio, stats: dict) -> dict:
    cantidad, ultima = stats.get(w.id, (0, None))
    return {
        "id": w.id,
        "codigo": "WK-P" + w.id[-4:].upper(),
        "nombre": w.nombre,
        "descripcion": w.descripcion,
        "categoria": w.categoria,
        "criticidad": w.criticidad,
        "ambientes": list(w.ambientes),
        "roles": list(w.roles),
        "integrado": False,
        # Aprobado: la versión vigente está validada en Sandbox y se puede ejecutar en PROD.
        "estado": "aprobado" if w.version_aprobada == w.version_actual else "pendiente_sandbox",
        "version_actual": w.version_actual,
        "version_aprobada": w.version_aprobada,
        "condicion": w.condicion,
        "pasos": list(w.pasos),
        "requiere_base": True,
        "requiere_ventana": False,
        "eventos_automaticos": [],
        "transaccional": w.transaccional,
        "creado_por": w.creado_por,
        "ejecuciones": cantidad,
        "ultima_ejecucion": ultima.isoformat() + "Z" if ultima else None,
    }


async def biblioteca(tenant: Tenant) -> list[dict]:
    stats = await run_in_threadpool(
        repositorios.estadisticas, tenant.base_sqlserver, str(tenant.id)
    )
    propios = await run_in_threadpool(
        repositorios.listar_propios, tenant.base_sqlserver, str(tenant.id)
    )
    return [_ficha_integrado(i, stats) for i in integrados.CATALOGO.values()] + [
        _ficha_propio(w, stats) for w in propios
    ]


async def _propio(tenant: Tenant, workaround_id: str) -> Propio:
    try:
        UUID(workaround_id)
    except ValueError:
        raise ErrorDominio("WKR_VAL_000", workaround_id) from None
    propio = await run_in_threadpool(
        repositorios.obtener_propio, tenant.base_sqlserver, str(tenant.id), workaround_id
    )
    if propio is None:
        raise ErrorDominio("WKR_VAL_000", workaround_id)
    return propio


async def detalle(tenant: Tenant, workaround_id: str) -> dict:
    stats = await run_in_threadpool(
        repositorios.estadisticas, tenant.base_sqlserver, str(tenant.id)
    )
    if workaround_id in integrados.CATALOGO:
        ficha = _ficha_integrado(integrados.CATALOGO[workaround_id], stats)
        ficha["versiones"] = []
    else:
        propio = await _propio(tenant, workaround_id)
        ficha = _ficha_propio(propio, stats)
        versiones = await run_in_threadpool(
            repositorios.listar_versiones, tenant.base_sqlserver, propio.id
        )
        ficha["versiones"] = [
            {
                "version": v.version,
                "pasos": list(v.pasos),
                "creada_por": v.creada_por,
                "creada_en": v.creada_en.isoformat() + "Z",
                "validada_en": v.validada_en.isoformat() + "Z" if v.validada_en else None,
            }
            for v in versiones
        ]
    ejecuciones = await run_in_threadpool(
        repositorios.listar_ejecuciones, tenant.base_sqlserver, str(tenant.id), workaround_id, 20
    )
    ficha["ultimas_ejecuciones"] = [ejecucion_a_dict(e) for e in ejecuciones]
    return ficha


# --- Propios (CU-000-005-A, B y C) --------------------------------------------------


def _validar_sintaxis(conexion: Conexion, base: str | None, pasos: tuple[str, ...]) -> None:
    """Compila cada paso sin ejecutarlo (NOEXEC): sintaxis y objetos referenciados."""
    try:
        with sqlserver.conectar_externo(
            conexiones.servidor_de(conexion),
            conexion.usuario_sql,
            cifrado.descifrar(conexion.password_sql_cifrada),
            base or sqlserver.BASE_SISTEMA,
        ) as abierta:
            cursor = abierta.cursor()
            cursor.execute("SET NOEXEC ON")
            try:
                for numero, paso in enumerate(pasos, start=1):
                    try:
                        cursor.execute(paso)
                        while cursor.nextset():
                            pass
                    except pyodbc.Error as error:
                        raise ErrorDominio("SQL_VAL_000", f"Paso {numero}: {error}") from error
            finally:
                cursor.execute("SET NOEXEC OFF")
    except ErrorDominio:
        raise
    except Exception as error:
        raise ErrorDominio("CON_DB_000", str(error)) from error


async def _validar_propio(tenant: Tenant, w: Propio, validar_en: tuple[str, str | None]) -> Propio:
    nombre = (w.nombre or "").strip()
    pasos = tuple(p.strip() for p in w.pasos if p and p.strip())
    ambientes = tuple(a for a in AMBIENTES if a in w.ambientes)
    roles = tuple(r for r in ROLES_EJECUTORES if r in w.roles)
    if (
        not nombre
        or len(nombre) > 100
        or not (w.descripcion or "").strip()
        or w.categoria not in CATEGORIAS
        or w.criticidad not in CRITICIDADES
        or not ambientes
        or not roles
        or not pasos
        or len(pasos) > MAXIMO_PASOS
        or any(len(p) > LARGO_PASO for p in pasos)
    ):
        raise ErrorDominio("USR_VAL_002", "Datos del workaround incompletos o inválidos.")
    if await run_in_threadpool(
        repositorios.existe_nombre, tenant.base_sqlserver, str(tenant.id), nombre, w.id or None
    ):
        raise ErrorDominio("WKR_VAL_001", nombre)
    conexion = await conexiones.obtener(tenant, validar_en[0])
    await run_in_threadpool(_validar_sintaxis, conexion, validar_en[1], pasos)
    return replace(
        w,
        nombre=nombre,
        descripcion=w.descripcion.strip(),
        condicion=(w.condicion or "").strip() or None,
        pasos=pasos,
        ambientes=ambientes,
        roles=roles,
    )


async def guardar_propio(
    tenant: Tenant,
    actor: Sesion,
    workaround_id: str | None,
    w: Propio,
    validar_en: tuple[str, str | None],
) -> dict:
    _exigir(actor, ROLES_CREAN, "workarounds.crear")
    anterior = await _propio(tenant, workaround_id) if workaround_id else None
    w = await _validar_propio(
        tenant, replace(w, id=workaround_id or "", tenant_id=str(tenant.id)), validar_en
    )
    nuevo_id = await run_in_threadpool(
        repositorios.guardar_propio, tenant.base_sqlserver, w, actor.email
    )
    await _auditar(
        tenant,
        actor.email,
        "workaround.creado" if anterior is None else "workaround.modificado",
        {
            "workaround_id": nuevo_id,
            "nombre": w.nombre,
            "version": 1 if anterior is None else anterior.version_actual + 1,
            "pasos": list(w.pasos),
        },
    )
    return await detalle(tenant, nuevo_id)


async def eliminar_propio(tenant: Tenant, actor: Sesion, workaround_id: str) -> None:
    _exigir(actor, ROLES_CREAN, "workarounds.eliminar")
    propio = await _propio(tenant, workaround_id)
    await run_in_threadpool(
        repositorios.eliminar_propio, tenant.base_sqlserver, str(tenant.id), workaround_id
    )
    await _auditar(
        tenant,
        actor.email,
        "workaround.eliminado",
        {"workaround_id": workaround_id, "nombre": propio.nombre},
    )


async def validar_en_sandbox(
    tenant: Tenant, actor: Sesion, workaround_id: str, conexion_id: str, base: str
) -> dict:
    """Ejecuta la versión vigente en el Sandbox y, si pasa, la aprueba para PROD.

    Si falla, la versión aprobada anterior sigue siendo la que corre en PROD
    (CU-000-005-B, flujo alternativo 6.0).
    """
    _exigir(actor, ROLES_CREAN, "workarounds.validar")
    propio = await _propio(tenant, workaround_id)
    sql = "\nGO\n".join(propio.pasos)
    resultado = await sandbox.ejecutar(tenant, actor, conexion_id, base, sql, False)
    validacion = {
        "registro_sandbox": resultado.get("id"),
        "conexion_id": conexion_id,
        "base": base,
        "estado": resultado["estado"],
        "error": resultado.get("error"),
        "duracion_ms": resultado.get("duracion_ms"),
        "filas": resultado.get("filas"),
        "avisos": resultado.get("avisos", []),
    }
    await _auditar(
        tenant,
        actor.email,
        "workaround.validado" if resultado["estado"] == "ok" else "workaround.validacion_fallida",
        {"workaround_id": workaround_id, "version": propio.version_actual, **validacion},
    )
    if resultado["estado"] != "ok":
        raise ErrorDominio("WKR_SBX_000", resultado.get("error"))
    await run_in_threadpool(
        repositorios.aprobar_version,
        tenant.base_sqlserver,
        str(tenant.id),
        workaround_id,
        propio.version_actual,
        validacion,
    )
    return {**await detalle(tenant, workaround_id), "validacion": validacion}


# --- Diagnóstico y ejecución (CU-000-005-E y F) -------------------------------------


def dentro_de_ventana(conexion: Conexion, ahora: datetime | None = None) -> bool:
    if conexion.ventana_inicio is None or conexion.ventana_fin is None:
        return False
    local = (ahora or datetime.now(UTC)).astimezone(ZoneInfo(conexion.zona_horaria)).time()
    inicio, fin = conexion.ventana_inicio, conexion.ventana_fin
    if inicio < fin:
        return inicio <= local < fin
    # La ventana cruza la medianoche (por ejemplo, 22:00 a 06:00).
    return local >= inicio or local < fin


def _leer_diagnostico(
    conexion: Conexion, base: str | None, integrado: integrados.Integrado
) -> dict:
    if integrado.id == "iniciar_servicio":
        if not conexion.tiene_winrm:
            raise ErrorDominio("WRM_000", "La conexión no tiene WinRM configurado.")
        try:
            resultado = winrm.ejecutar_powershell(
                conexion.host,
                conexion.winrm_puerto,
                conexion.winrm_https,
                conexion.winrm_usuario,
                cifrado.descifrar(conexion.winrm_password_cifrada),
                integrados.SCRIPT_ESTADO_SERVICIOS,
            )
            return {"servicios": integrados.servicios_desde_json(resultado.salida)}
        except ErrorDominio:
            raise
        except Exception as error:
            raise ErrorDominio("WRM_000", str(error)) from error
    try:
        with sqlserver.conectar_externo(
            conexiones.servidor_de(conexion),
            conexion.usuario_sql,
            cifrado.descifrar(conexion.password_sql_cifrada),
            base or sqlserver.BASE_SISTEMA,
        ) as abierta:
            return integrados.diagnosticar(integrado, abierta)
    except Exception as error:
        raise ErrorDominio("CON_DB_000", str(error)) from error


async def _preparar(
    tenant: Tenant, actor_rol: str | None, workaround_id: str, conexion_id: str, base: str | None
) -> tuple[Conexion, integrados.Integrado | Propio]:
    conexion = await conexiones.obtener(tenant, conexion_id)
    if workaround_id in integrados.CATALOGO:
        workaround = integrados.CATALOGO[workaround_id]
        requiere_base = workaround.requiere_base
    else:
        workaround = await _propio(tenant, workaround_id)
        requiere_base = True
    if actor_rol is not None and actor_rol not in workaround.roles:
        raise ErrorDominio("USR_PER_000", "workarounds.ejecutar")
    if conexion.ambiente not in workaround.ambientes:
        raise ErrorDominio("WKR_AMB_000", conexion.ambiente)
    if requiere_base and not (base or "").strip():
        raise ErrorDominio("USR_VAL_002", "Elegí la base de datos.")
    return conexion, workaround


async def diagnostico(
    tenant: Tenant,
    actor: Sesion,
    workaround_id: str,
    conexion_id: str,
    base: str | None,
    parametros: dict,
) -> dict:
    """Lo que se va a hacer, antes de confirmar: diagnóstico y pasos (CU-000-005-E, paso 5)."""
    conexion, workaround = await _preparar(tenant, actor.rol, workaround_id, conexion_id, base)
    if isinstance(workaround, Propio):
        version, pasos_sql = await _version_a_ejecutar(tenant, workaround, conexion)
        pasos = [Paso(f"Paso {n}", sql=p) for n, p in enumerate(pasos_sql, 1)]
        return {
            "diagnostico": {"version": version},
            "pasos": ejecutor.pasos_iniciales(pasos),
            **_condiciones(conexion, workaround),
        }
    datos = await run_in_threadpool(_leer_diagnostico, conexion, base, workaround)
    try:
        pasos = integrados.pasos(workaround, base, datos, parametros)
    except ErrorDominio:
        # Sin elección todavía (por ejemplo, qué sesiones terminar): solo el diagnóstico.
        pasos = []
    return {
        "diagnostico": datos,
        "pasos": ejecutor.pasos_iniciales(pasos),
        **_condiciones(conexion, workaround),
    }


def _condiciones(conexion: Conexion, workaround) -> dict:
    requiere_ventana = getattr(workaround, "requiere_ventana", False)
    return {
        "prod": conexion.ambiente == "PROD",
        "requiere_ventana": requiere_ventana,
        "dentro_de_ventana": dentro_de_ventana(conexion) if requiere_ventana else True,
        "ventana": (
            f"{conexion.ventana_inicio:%H:%M}–{conexion.ventana_fin:%H:%M} "
            f"({conexion.zona_horaria})"
            if conexion.ventana_inicio
            else None
        ),
    }


async def _version_a_ejecutar(
    tenant: Tenant, w: Propio, conexion: Conexion
) -> tuple[int, tuple[str, ...]]:
    """En PROD se ejecuta solo la última versión validada en Sandbox (CU-000-005-B)."""
    if conexion.ambiente != "PROD":
        return w.version_actual, w.pasos
    if w.version_aprobada is None:
        raise ErrorDominio("WKR_SBX_001", w.nombre)
    if w.version_aprobada == w.version_actual:
        return w.version_actual, w.pasos
    pasos = await run_in_threadpool(
        repositorios.pasos_de_version, tenant.base_sqlserver, w.id, w.version_aprobada
    )
    return w.version_aprobada, pasos


async def ejecutar(
    tenant: Tenant,
    actor: Sesion | None,
    workaround_id: str,
    conexion_id: str,
    base: str | None,
    parametros: dict,
    origen: str,
    alerta_id: int | None = None,
) -> int:
    """Valida permisos y condiciones y lanza la ejecución. Devuelve el id de ejecución.

    ``actor`` None es una ejecución automática configurada por un DBA.
    """
    if origen not in ORIGENES:
        raise ErrorDominio("USR_VAL_002", "Origen inválido.")
    conexion, workaround = await _preparar(
        tenant, actor.rol if actor else None, workaround_id, conexion_id, base
    )
    usuario = actor.email if actor else USUARIO_AUTOMATICO

    if actor:
        datos_usuario = await run_in_threadpool(
            usuarios.obtener, tenant.base_sqlserver, str(tenant.id), actor.usuario_id
        )
        if datos_usuario is None or conexion.ambiente not in datos_usuario.ambientes:
            raise ErrorDominio("USR_PER_000", f"ambiente {conexion.ambiente}")
        if conexion.ambiente == "PROD":
            reautenticacion.exigir_segundo_factor_reciente(actor)

    autorizada_por = None
    if isinstance(workaround, Propio):
        version, pasos_sql = await _version_a_ejecutar(tenant, workaround, conexion)
        pasos = [Paso(f"Paso {n}", sql=p) for n, p in enumerate(pasos_sql, 1)]
        transaccional = workaround.transaccional
    else:
        version = None
        if workaround.requiere_ventana and not dentro_de_ventana(conexion):
            autorizada_por = await run_in_threadpool(
                repositorios.consumir_autorizacion,
                tenant.base_sqlserver,
                str(tenant.id),
                workaround.id,
                conexion.id,
                usuario,
            )
            if autorizada_por is None:
                raise ErrorDominio("WKR_VEN_000")
        datos = await run_in_threadpool(_leer_diagnostico, conexion, base, workaround)
        pasos = integrados.pasos(workaround, base, datos, parametros)
        transaccional = workaround.transaccional
    if not pasos:
        raise ErrorDominio("WKR_EJE_002")

    registro = Ejecucion(
        id=0,
        workaround=workaround.id,
        workaround_nombre=workaround.nombre,
        version=version,
        conexion_id=conexion.id,
        conexion_alias=conexion.alias,
        ambiente=conexion.ambiente,
        base=base,
        parametros=parametros,
        alerta_id=alerta_id,
        origen=origen,
        estado="en_curso",
        pasos=ejecutor.pasos_iniciales(pasos),
        ejecutada_por=usuario,
        autorizada_por=autorizada_por,
    )
    registro.id = await run_in_threadpool(
        repositorios.crear_ejecucion, tenant.base_sqlserver, str(tenant.id), registro
    )
    tarea = asyncio.create_task(_correr(tenant, conexion, registro, pasos, transaccional))
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)
    return registro.id


async def _correr(
    tenant: Tenant, conexion: Conexion, registro: Ejecucion, pasos: list[Paso], transaccional: bool
) -> None:
    def progreso(estado_pasos: list[dict], estado: str, error: str | None) -> None:
        repositorios.actualizar_ejecucion(
            tenant.base_sqlserver, registro.id, estado_pasos, estado, error
        )

    # Histórico de schema: solo los propios pueden cambiar estructura (los integrados
    # hacen mantenimiento).
    sql = "\nGO\n".join(p.sql for p in pasos if p.sql)
    previa = await historico.preparar(conexion, registro.base, sql) if registro.version else None
    try:
        estado, error = await run_in_threadpool(
            ejecutor.ejecutar, conexion, registro.base, pasos, transaccional, progreso
        )
    except Exception as falla:
        log.exception("WKR_EJE_000 ejecución %s", registro.id)
        estado, error = "fallida", str(falla)
    if estado == "exitosa":
        await historico.registrar(
            tenant,
            previa,
            sql,
            registro.ejecutada_por,
            "workaround",
            str(registro.id),
            autorizado_por=registro.autorizada_por,
        )
    final = await run_in_threadpool(
        repositorios.obtener_ejecucion, tenant.base_sqlserver, str(tenant.id), registro.id
    )
    # Bitácora de transacciones (TFI): todo lo ejecutado sobre la instancia, paso a paso.
    await auditoria.registrar_transaccion(
        tenant.base_mongodb,
        EventoAuditoria(
            accion="workaround.ejecutado",
            usuario=registro.ejecutada_por,
            tenant_id=str(tenant.id),
            detalle={
                **ejecucion_a_dict(final),
                "mobile": registro.origen == "mobile",
            },
        ),
    )
    # Una falla (o una ejecución sin intervención humana) se informa al equipo DBA.
    if estado != "exitosa" or registro.origen == "automatica":
        clave = "automatica" if registro.origen == "automatica" else "manual"
        await notificaciones.avisar(
            tenant,
            ROLES_OPERATIVOS,
            f"{registro.workaround_nombre} · {conexion.alias}",
            traducir(f"workarounds.aviso.{clave}").format(
                estado=traducir(f"workarounds.estado.{estado}"), error=error or "—"
            ),
            f"/workarounds?ejecucion={registro.id}",
        )


def ejecucion_a_dict(e: Ejecucion) -> dict:
    return {
        **{k: v for k, v in e.__dict__.items() if not k.endswith("_en")},
        "iniciada_en": e.iniciada_en.isoformat() + "Z" if e.iniciada_en else None,
        "finalizada_en": e.finalizada_en.isoformat() + "Z" if e.finalizada_en else None,
    }


async def obtener_ejecucion(tenant: Tenant, ejecucion_id: int) -> dict:
    e = await run_in_threadpool(
        repositorios.obtener_ejecucion, tenant.base_sqlserver, str(tenant.id), ejecucion_id
    )
    if e is None:
        raise ErrorDominio("WKR_VAL_000", str(ejecucion_id))
    return ejecucion_a_dict(e)


async def ejecuciones(tenant: Tenant) -> list[dict]:
    return [
        ejecucion_a_dict(e)
        for e in await run_in_threadpool(
            repositorios.listar_ejecuciones, tenant.base_sqlserver, str(tenant.id)
        )
    ]


async def bases(tenant: Tenant, conexion_id: str) -> list[str]:
    conexion = await conexiones.obtener(tenant, conexion_id)

    def leer() -> list[str]:
        try:
            with sqlserver.conectar_externo(
                conexiones.servidor_de(conexion),
                conexion.usuario_sql,
                cifrado.descifrar(conexion.password_sql_cifrada),
            ) as abierta:
                filas = abierta.cursor().execute(
                    "SELECT name FROM sys.databases WHERE state_desc = 'ONLINE' ORDER BY name"
                )
                return [f[0] for f in filas.fetchall()]
        except Exception as error:
            raise ErrorDominio("CON_DB_000", str(error)) from error

    return await run_in_threadpool(leer)


async def cerrar_interrumpidas_todo(tenants: list[Tenant]) -> None:
    for tenant in tenants:
        try:
            await run_in_threadpool(
                repositorios.cerrar_interrumpidas, tenant.base_sqlserver, str(tenant.id)
            )
        except Exception:
            log.exception("No se pudieron cerrar las ejecuciones interrumpidas de %s.", tenant.slug)


# --- Autorización fuera de la ventana de mantenimiento ------------------------------


async def solicitar_autorizacion(
    tenant: Tenant, actor: Sesion, workaround_id: str, conexion_id: str, motivo: str
) -> None:
    motivo = (motivo or "").strip()
    if not motivo or len(motivo) > 500:
        raise ErrorDominio("USR_VAL_002", "El motivo es obligatorio.")
    conexion, workaround = await _preparar(tenant, actor.rol, workaround_id, conexion_id, "-")
    if not getattr(workaround, "requiere_ventana", False):
        raise ErrorDominio("USR_VAL_002", "Este workaround no requiere autorización.")
    await run_in_threadpool(
        repositorios.crear_autorizacion,
        tenant.base_sqlserver,
        str(tenant.id),
        (workaround.id, workaround.nombre),
        (conexion.id, conexion.alias),
        motivo,
        actor.email,
        VIGENCIA_PEDIDO_HORAS,
    )
    await _auditar(
        tenant,
        actor.email,
        "workaround.autorizacion_solicitada",
        {"workaround": workaround.id, "conexion": conexion.alias, "motivo": motivo},
    )
    await notificaciones.avisar(
        tenant,
        tuple(ROLES_AUTORIZAN),
        traducir("workarounds.aviso.autorizacion_titulo").format(workaround=workaround.nombre),
        traducir("workarounds.aviso.autorizacion").format(
            usuario=actor.email,
            workaround=workaround.nombre,
            conexion=conexion.alias,
            motivo=motivo,
        ),
        "/workarounds?autorizaciones=1",
    )


async def autorizaciones(tenant: Tenant) -> list[dict]:
    lista = await run_in_threadpool(
        repositorios.listar_autorizaciones, tenant.base_sqlserver, str(tenant.id)
    )
    return [
        {
            **{k: v for k, v in a.__dict__.items() if not k.endswith("_en")},
            "solicitada_en": a.solicitada_en.isoformat() + "Z",
            "resuelta_en": a.resuelta_en.isoformat() + "Z" if a.resuelta_en else None,
            "vence_en": a.vence_en.isoformat() + "Z",
        }
        for a in lista
    ]


async def resolver_autorizacion(
    tenant: Tenant, actor: Sesion, autorizacion_id: str, aprobar: bool
) -> None:
    _exigir(actor, ROLES_AUTORIZAN, "workarounds.autorizar")
    try:
        UUID(autorizacion_id)
    except ValueError:
        raise ErrorDominio("WKR_AUT_000", autorizacion_id) from None
    pedido = await run_in_threadpool(
        repositorios.obtener_autorizacion, tenant.base_sqlserver, str(tenant.id), autorizacion_id
    )
    if pedido is None:
        raise ErrorDominio("WKR_AUT_000", autorizacion_id)
    # La autorización la da otra persona: nadie aprueba su propio pedido.
    if pedido.solicitada_por == actor.email:
        raise ErrorDominio("WKR_AUT_001")
    if not await run_in_threadpool(
        repositorios.resolver_autorizacion,
        tenant.base_sqlserver,
        str(tenant.id),
        autorizacion_id,
        actor.email,
        aprobar,
        VIGENCIA_AUTORIZACION_HORAS,
    ):
        raise ErrorDominio("WKR_AUT_000", autorizacion_id)
    await _auditar(
        tenant,
        actor.email,
        "workaround.autorizacion_aprobada" if aprobar else "workaround.autorizacion_rechazada",
        {
            "autorizacion_id": autorizacion_id,
            "workaround": pedido.workaround,
            "solicitante": pedido.solicitada_por,
        },
    )


# --- Ejecución automática -----------------------------------------------------------


async def automatizaciones(tenant: Tenant) -> list[dict]:
    return [
        a.__dict__
        for a in await run_in_threadpool(
            repositorios.listar_automatizaciones, tenant.base_sqlserver, str(tenant.id)
        )
    ]


async def crear_automatizacion(
    tenant: Tenant, actor: Sesion, evento: str, workaround_id: str, conexion_id: str | None
) -> dict:
    _exigir(actor, ROLES_CREAN, "workarounds.automatizar")
    integrado = integrados.CATALOGO.get(workaround_id)
    if (
        evento not in EVENTOS_AUTOMATIZABLES
        or integrado is None
        or evento not in integrado.eventos_automaticos
    ):
        raise ErrorDominio("WKR_AUT_002")
    conexion = await conexiones.obtener(tenant, conexion_id) if conexion_id else None
    # Habilitar ejecuciones sin intervención humana sobre PROD exige segundo factor;
    # "todas las conexiones" incluye las de PROD.
    if conexion is None or conexion.ambiente == "PROD":
        reautenticacion.exigir_segundo_factor_reciente(actor)
    automatizacion = Automatizacion("", evento, workaround_id, conexion_id, True, actor.email)
    automatizacion_id = await run_in_threadpool(
        repositorios.crear_automatizacion, tenant.base_sqlserver, str(tenant.id), automatizacion
    )
    await _auditar(
        tenant,
        actor.email,
        "workaround.automatizacion_creada",
        {
            "evento": evento,
            "workaround": workaround_id,
            "conexion": conexion.alias if conexion else None,
        },
    )
    return {**automatizacion.__dict__, "id": automatizacion_id}


async def eliminar_automatizacion(tenant: Tenant, actor: Sesion, automatizacion_id: str) -> None:
    _exigir(actor, ROLES_CREAN, "workarounds.automatizar")
    try:
        UUID(automatizacion_id)
    except ValueError:
        raise ErrorDominio("WKR_AUT_000", automatizacion_id) from None
    if not await run_in_threadpool(
        repositorios.eliminar_automatizacion,
        tenant.base_sqlserver,
        str(tenant.id),
        automatizacion_id,
    ):
        raise ErrorDominio("WKR_AUT_000", automatizacion_id)
    await _auditar(
        tenant, actor.email, "workaround.automatizacion_eliminada", {"id": automatizacion_id}
    )


def evento_de_clave(clave: str) -> tuple[str, str | None] | None:
    """Evento automatizable de la condición de una alerta y la base a la que aplica."""
    if clave == "evento:instancia":
        return "instancia", None
    if clave.startswith("evento:log:"):
        return "log", clave.removeprefix("evento:log:")
    if clave.startswith("evento:base:"):
        return "base", clave.removeprefix("evento:base:")
    return None


class Automatizador:
    """Ejecuta los workarounds configurados cuando se dispara una alerta nueva."""

    async def alerta_disparada(self, tenant: Tenant, conexion: Conexion, alerta) -> None:
        evento = evento_de_clave(alerta.clave)
        if evento is None:
            return
        tipo, base = evento
        configuradas = await run_in_threadpool(
            repositorios.listar_automatizaciones, tenant.base_sqlserver, str(tenant.id)
        )
        for a in configuradas:
            if not a.activa or a.evento != tipo or (a.conexion_id and a.conexion_id != conexion.id):
                continue
            parametros: dict = {}
            destino = base
            if a.workaround == "iniciar_servicio":
                servicio = f"MSSQL${conexion.instancia}" if conexion.instancia else "MSSQLSERVER"
                parametros = {"servicio": servicio}
            if a.workaround == "base_online":
                # La base afectada es el parámetro: el workaround corre desde master.
                parametros, destino = {"base": base}, None
            try:
                await ejecutar(
                    tenant,
                    None,
                    a.workaround,
                    conexion.id,
                    destino,
                    parametros,
                    "automatica",
                    alerta.id,
                )
            except ErrorDominio as error:
                log.warning("Ejecución automática de %s no realizada: %s", a.workaround, error)
                await notificaciones.avisar(
                    tenant,
                    ROLES_OPERATIVOS,
                    traducir("workarounds.aviso.no_realizada_titulo").format(
                        conexion=conexion.alias
                    ),
                    traducir("workarounds.aviso.no_realizada").format(
                        workaround=a.workaround, alerta=alerta.id, error=error.definicion.mensaje
                    ),
                    f"/alertas?id={alerta.id}",
                )
