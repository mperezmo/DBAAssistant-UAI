"""Casos de uso del contexto de negocio (CU-000-006 A a E) y su aplicación.

El contexto cumple dos funciones: orienta al motor IA (glosario, reglas y tablas que
conviene evitar) y fija restricciones que el sistema verifica antes de ejecutar
(roles por tabla, enmascaramiento, doble confirmación y reglas de bloqueo o advertencia).
"""

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool

from app.core.errores import ErrorDominio
from app.identidad.entidades import AMBIENTES, ROLES, Sesion
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.chat_ia.analizador import Analisis, Tabla
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import Conexion
from app.modulos.contexto_negocio import documentos, esquema, repositorios
from app.modulos.contexto_negocio.entidades import (
    ACCIONES,
    CLASES_DOCUMENTO,
    EFECTOS,
    ERROR,
    LARGO_MAXIMO_RESUMEN,
    LISTO,
    MAXIMO_BYTES_DOCUMENTO,
    PRIORIDADES,
    PROCESANDO,
    Documento,
    Regla,
    TablaSensible,
    Termino,
)
from app.tenants.entidades import Tenant

log = logging.getLogger(__name__)

ROLES_EDITAN = frozenset({"ADMINISTRADOR", "DBA"})
TIPOS = ("sensible", "termino", "regla", "documento")
# Un resumen que sigue "procesando" pasado este tiempo quedó cortado (por ejemplo, por un
# reinicio) y se puede volver a generar.
VENCIMIENTO_PROCESANDO = timedelta(minutes=15)

_tareas: set[asyncio.Task] = set()


def puede_editar(sesion: Sesion) -> bool:
    return sesion.rol in ROLES_EDITAN


def _exigir_edicion(sesion: Sesion) -> None:
    if not puede_editar(sesion):
        raise ErrorDominio("USR_PER_000", "contexto.editar")


async def _auditar(tenant: Tenant, actor: Sesion, accion: str, detalle: dict) -> None:
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion=accion, usuario=actor.email, tenant_id=str(tenant.id), detalle=detalle
        ),
    )


def _texto(valor: str | None) -> str:
    return (valor or "").strip()


def _hora(valor: time | None) -> str | None:
    return valor.strftime("%H:%M") if valor else None


# --- Consulta (CU-000-006-E) --------------------------------------------------------


async def listar(tenant: Tenant, conexion_id: str, base: str | None) -> dict:
    await conexiones.obtener(tenant, conexion_id)
    por_base = {"conexion_id": conexion_id, **({"base": base} if base else {})}
    reglas = await repositorios.listar(
        tenant.base_mongodb,
        str(tenant.id),
        {"tipo": "regla", "conexion_id": {"$in": [None, conexion_id]}},
    )
    if base:
        reglas = [r for r in reglas if r.get("base") in (None, base)]
    return {
        "sensibles": await repositorios.listar(
            tenant.base_mongodb, str(tenant.id), {"tipo": "sensible", **por_base}
        ),
        "terminos": await repositorios.listar(
            tenant.base_mongodb, str(tenant.id), {"tipo": "termino", **por_base}
        ),
        "reglas": reglas,
        "documentos": [
            _documento_visible(d)
            for d in await repositorios.listar(
                tenant.base_mongodb,
                str(tenant.id),
                {
                    "tipo": "documento",
                    "conexion_id": conexion_id,
                    **({"base": {"$in": [None, base]}} if base else {}),
                },
            )
        ],
    }


# --- Alta y modificación (CU-000-006 A, B, C y D) ----------------------------------


async def _anterior(tenant: Tenant, tipo: str, elemento_id: str | None) -> dict | None:
    if elemento_id is None:
        return None
    anterior = await repositorios.obtener(tenant.base_mongodb, str(tenant.id), tipo, elemento_id)
    if anterior is None:
        raise ErrorDominio("CTX_VAL_000", elemento_id)
    return anterior


async def _guardar(
    tenant: Tenant,
    actor: Sesion,
    tipo: str,
    elemento_id: str | None,
    documento: dict,
    codigo_duplicado: str,
) -> dict:
    anterior = await _anterior(tenant, tipo, elemento_id)
    try:
        nuevo_id = await repositorios.guardar(
            tenant.base_mongodb, str(tenant.id), elemento_id, {"tipo": tipo, **documento}
        )
    except repositorios.Duplicado:
        raise ErrorDominio(codigo_duplicado, documento.get("clave")) from None
    guardado = await repositorios.obtener(tenant.base_mongodb, str(tenant.id), tipo, nuevo_id)
    # CU-000-006-D: la modificación registra el valor anterior y el nuevo.
    await _auditar(
        tenant,
        actor,
        f"contexto.{tipo}_{'modificado' if anterior else 'creado'}",
        {
            "id": nuevo_id,
            "anterior": {k: v for k, v in (anterior or {}).items() if k != "actualizado_en"}
            or None,
            "nuevo": {k: v for k, v in guardado.items() if k != "actualizado_en"},
        },
    )
    return guardado


async def guardar_sensible(
    tenant: Tenant, actor: Sesion, elemento_id: str | None, datos: TablaSensible
) -> dict:
    _exigir_edicion(actor)
    base, tabla = _texto(datos.base), _texto(datos.tabla)
    roles_consulta = tuple(r for r in ROLES if r in datos.roles_consulta)
    roles_ven = tuple(r for r in ROLES if r in datos.roles_ven_columnas)
    if not base or not tabla or not roles_consulta:
        raise ErrorDominio("USR_VAL_002", "Base, tabla y roles que consultan son obligatorios.")
    encontrada = esquema.buscar_tabla(await esquema.obtener(tenant, datos.conexion_id), base, tabla)
    if encontrada is None:
        raise ErrorDominio("CTX_VAL_003", f"{base}.{tabla}")
    existentes = {c["nombre"].lower(): c["nombre"] for c in encontrada["columnas"]}
    columnas = []
    for columna in datos.columnas:
        if columna.lower() not in existentes:
            raise ErrorDominio("CTX_VAL_003", f"{tabla}.{columna}")
        columnas.append(existentes[columna.lower()])
    # Coherencia: ver las columnas sin máscara exige poder consultar la tabla, y la marca
    # tiene que restringir algo.
    sin_restriccion = (
        not columnas and len(roles_consulta) == len(ROLES) and not datos.doble_confirmacion
    )
    if not set(roles_ven) <= set(roles_consulta) or sin_restriccion:
        raise ErrorDominio("CTX_VAL_002", tabla)
    nombre = f"{encontrada['esquema']}.{encontrada['nombre']}"
    documento = {
        "conexion_id": datos.conexion_id,
        "base": base,
        "tabla": nombre,
        "clave": nombre.lower(),
        "columnas": columnas,
        "roles_consulta": list(roles_consulta),
        "roles_ven_columnas": list(roles_ven),
        "doble_confirmacion": datos.doble_confirmacion,
        "descripcion": _texto(datos.descripcion)[:500],
    }
    return await _guardar(tenant, actor, "sensible", elemento_id, documento, "CTX_VAL_002")


async def guardar_termino(
    tenant: Tenant, actor: Sesion, elemento_id: str | None, datos: Termino
) -> dict:
    _exigir_edicion(actor)
    termino, base, entidad = _texto(datos.termino), _texto(datos.base), _texto(datos.entidad)
    if not termino or not base or not entidad:
        raise ErrorDominio("USR_VAL_002", "Término, base y entidad son obligatorios.")
    encontrada = esquema.buscar_tabla(
        await esquema.obtener(tenant, datos.conexion_id), base, entidad
    )
    if encontrada is None:
        raise ErrorDominio("CTX_VAL_003", f"{base}.{entidad}")
    sinonimos = list(dict.fromkeys(s.strip() for s in datos.sinonimos if s.strip()))
    documento = {
        "conexion_id": datos.conexion_id,
        "base": base,
        "termino": termino,
        "clave": termino.lower(),
        "entidad": f"{encontrada['esquema']}.{encontrada['nombre']}",
        "sinonimos": sinonimos[:20],
        "descripcion": _texto(datos.descripcion)[:1000],
    }
    return await _guardar(tenant, actor, "termino", elemento_id, documento, "CTX_VAL_004")


async def guardar_regla(
    tenant: Tenant, actor: Sesion, elemento_id: str | None, datos: Regla
) -> dict:
    _exigir_edicion(actor)
    descripcion = _texto(datos.descripcion)
    acciones = [a for a in ACCIONES if a in datos.acciones]
    if not descripcion or not acciones:
        raise ErrorDominio("USR_VAL_002", "Descripción y acciones son obligatorias.")
    if datos.efecto not in EFECTOS or datos.prioridad not in PRIORIDADES:
        raise ErrorDominio("USR_VAL_002", "Efecto o prioridad inválidos.")
    if (datos.desde is None) != (datos.hasta is None) or (
        datos.desde is not None and datos.desde == datos.hasta
    ):
        raise ErrorDominio("USR_VAL_002", "Franja horaria inválida.")
    if datos.conexion_id:
        await conexiones.obtener(tenant, datos.conexion_id)
    documento = {
        "descripcion": descripcion[:500],
        "acciones": acciones,
        "efecto": datos.efecto,
        "prioridad": datos.prioridad,
        "conexion_id": datos.conexion_id or None,
        "base": _texto(datos.base) or None,
        "ambientes": [a for a in AMBIENTES if a in datos.ambientes],
        "tablas": list(dict.fromkeys(t.strip() for t in datos.tablas if t.strip()))[:50],
        "desde": _hora(datos.desde),
        "hasta": _hora(datos.hasta),
        "activa": datos.activa,
    }
    return await _guardar(tenant, actor, "regla", elemento_id, documento, "CTX_VAL_002")


async def eliminar(tenant: Tenant, actor: Sesion, tipo: str, elemento_id: str) -> None:
    _exigir_edicion(actor)
    if tipo not in TIPOS:
        raise ErrorDominio("CTX_VAL_000", tipo)
    anterior = await _anterior(tenant, tipo, elemento_id)
    await repositorios.eliminar(tenant.base_mongodb, str(tenant.id), tipo, elemento_id)
    if tipo == "documento":
        await repositorios.eliminar_archivo(tenant.base_mongodb, anterior["archivo_id"])
    await _auditar(
        tenant,
        actor,
        f"contexto.{tipo}_eliminado",
        {
            "id": elemento_id,
            "anterior": {k: v for k, v in anterior.items() if k != "actualizado_en"},
        },
    )


# --- Documentos de negocio ------------------------------------------------------------


def _documento_visible(documento: dict) -> dict:
    """Un resumen que quedó a medias no sigue figurando como "procesando"."""
    if documento.get("estado") == PROCESANDO:
        desde = datetime.fromisoformat(documento["actualizado_en"])
        if datetime.now(UTC) - desde > VENCIMIENTO_PROCESANDO:
            return {**documento, "estado": ERROR, "error": "interrumpido"}
    return documento


async def _documento(tenant: Tenant, documento_id: str) -> dict:
    documento = await repositorios.obtener(
        tenant.base_mongodb, str(tenant.id), "documento", documento_id
    )
    if documento is None:
        raise ErrorDominio("CTX_VAL_000", documento_id)
    return documento


async def cargar_documento(
    tenant: Tenant, actor: Sesion, datos: Documento, contenido: bytes
) -> dict:
    """Guarda el PDF y arranca su resumen en segundo plano."""
    _exigir_edicion(actor)
    if datos.clase not in CLASES_DOCUMENTO:
        raise ErrorDominio("USR_VAL_002", "Tipo de documento inválido.")
    if len(contenido) > MAXIMO_BYTES_DOCUMENTO:
        raise ErrorDominio("CTX_DOC_002")
    conexion = await conexiones.obtener(tenant, datos.conexion_id)
    base = _texto(datos.base) or None
    if base is not None:
        bases = await esquema.obtener(tenant, conexion.id)
        base = next((b for b in bases if b.lower() == base.lower()), None)
        if base is None:
            raise ErrorDominio("USR_VAL_002", f"Base inexistente o sin acceso: {datos.base}")
    nombre = _texto(datos.nombre)[:200] or "documento.pdf"
    texto, paginas = await run_in_threadpool(documentos.extraer_texto, contenido)

    archivo_id = await repositorios.guardar_archivo(
        tenant.base_mongodb, str(tenant.id), nombre, contenido
    )
    documento_id = await repositorios.guardar(
        tenant.base_mongodb,
        str(tenant.id),
        None,
        {
            "tipo": "documento",
            "conexion_id": conexion.id,
            "base": base,
            "clase": datos.clase,
            "nombre": nombre,
            "descripcion": _texto(datos.descripcion)[:500],
            "archivo_id": archivo_id,
            "bytes": len(contenido),
            "paginas": paginas,
            "caracteres": len(texto),
            "estado": PROCESANDO,
            "resumen": "",
            "tablas": [],
            "editado": False,
            "cargado_por": actor.email,
            "cargado_en": datetime.now(UTC).isoformat(),
        },
    )
    await _auditar(
        tenant,
        actor,
        "contexto.documento_cargado",
        {
            "id": documento_id,
            "conexion_id": conexion.id,
            "base": base,
            "clase": datos.clase,
            "nombre": nombre,
            "bytes": len(contenido),
            "paginas": paginas,
        },
    )
    _resumir_en_segundo_plano(tenant, conexion, documento_id, texto)
    return await _documento(tenant, documento_id)


def _resumir_en_segundo_plano(
    tenant: Tenant, conexion: Conexion, documento_id: str, texto: str
) -> None:
    tarea = asyncio.create_task(_resumir(tenant, conexion, documento_id, texto))
    _tareas.add(tarea)
    tarea.add_done_callback(_tareas.discard)


async def _resumir(tenant: Tenant, conexion: Conexion, documento_id: str, texto: str) -> None:
    campos: dict
    try:
        documento = await _documento(tenant, documento_id)
        resultado = await documentos.resumir(
            await _texto_esquema(tenant, conexion, documento["base"]),
            documento["nombre"],
            documento["clase"],
            documento["descripcion"],
            texto,
        )
        campos = {
            "estado": LISTO,
            "resumen": resultado.resumen[:LARGO_MAXIMO_RESUMEN],
            "tablas": resultado.tablas[:100],
            "editado": False,
            "error": None,
        }
    except ErrorDominio as error:
        log.warning("%s resumen del documento %s: %s", error.codigo, documento_id, error.detalle)
        campos = {"estado": ERROR, "error": error.codigo}
    except Exception:
        log.exception("Falló el resumen del documento %s", documento_id)
        campos = {"estado": ERROR, "error": "IA_TRA_000"}
    await repositorios.actualizar(
        tenant.base_mongodb, str(tenant.id), "documento", documento_id, campos
    )


async def regenerar_resumen(tenant: Tenant, actor: Sesion, documento_id: str) -> dict:
    """Vuelve a resumir el documento desde el PDF guardado (descarta la edición manual)."""
    _exigir_edicion(actor)
    documento = await _documento(tenant, documento_id)
    contenido = await repositorios.leer_archivo(
        tenant.base_mongodb, str(tenant.id), documento["archivo_id"]
    )
    if contenido is None:
        raise ErrorDominio("CTX_VAL_000", documento_id)
    texto, _ = await run_in_threadpool(documentos.extraer_texto, contenido)
    conexion = await conexiones.obtener(tenant, documento["conexion_id"])
    await repositorios.actualizar(
        tenant.base_mongodb,
        str(tenant.id),
        "documento",
        documento_id,
        {"estado": PROCESANDO, "error": None},
    )
    await _auditar(tenant, actor, "contexto.documento_resumen_regenerado", {"id": documento_id})
    _resumir_en_segundo_plano(tenant, conexion, documento_id, texto)
    return await _documento(tenant, documento_id)


async def guardar_resumen(tenant: Tenant, actor: Sesion, documento_id: str, resumen: str) -> dict:
    """Corrección manual del resumen: es lo que recibe la IA."""
    _exigir_edicion(actor)
    documento = await _documento(tenant, documento_id)
    resumen = _texto(resumen)
    if not resumen:
        raise ErrorDominio("USR_VAL_002", "El resumen no puede quedar vacío.")
    if len(resumen) > LARGO_MAXIMO_RESUMEN:
        raise ErrorDominio("USR_VAL_002", "El resumen es demasiado largo.")
    await repositorios.actualizar(
        tenant.base_mongodb,
        str(tenant.id),
        "documento",
        documento_id,
        {"resumen": resumen, "editado": True, "estado": LISTO, "error": None},
    )
    await _auditar(
        tenant,
        actor,
        "contexto.documento_resumen_modificado",
        {"id": documento_id, "anterior": documento["resumen"], "nuevo": resumen},
    )
    return await _documento(tenant, documento_id)


async def descargar_documento(tenant: Tenant, documento_id: str) -> tuple[str, bytes]:
    documento = await _documento(tenant, documento_id)
    contenido = await repositorios.leer_archivo(
        tenant.base_mongodb, str(tenant.id), documento["archivo_id"]
    )
    if contenido is None:
        raise ErrorDominio("CTX_VAL_000", documento_id)
    return documento["nombre"], contenido


# --- Aplicación de las restricciones --------------------------------------------------


def _misma_tabla(tabla: Tabla, base: str, nombre: str) -> bool:
    partes = nombre.lower().split(".")
    esquema_tabla, tabla_nombre = (
        (partes[0], partes[1]) if len(partes) == 2 else ("dbo", partes[-1])
    )
    return (
        tabla.base.lower() == base.lower()
        and tabla.esquema.lower() == esquema_tabla
        and tabla.nombre.lower() == tabla_nombre
    )


async def sensibles_de(tenant: Tenant, conexion_id: str, tablas: tuple[Tabla, ...]) -> list[dict]:
    """Marcas de sensibilidad que alcanzan a las tablas de una sentencia."""
    if not tablas:
        return []
    marcas = await repositorios.listar(
        tenant.base_mongodb, str(tenant.id), {"tipo": "sensible", "conexion_id": conexion_id}
    )
    return [m for m in marcas if any(_misma_tabla(t, m["base"], m["tabla"]) for t in tablas)]


def verificar_acceso(sesion: Sesion, sensibles: list[dict]) -> None:
    for marca in sensibles:
        if sesion.rol not in marca["roles_consulta"]:
            raise ErrorDominio(
                "USR_PER_000",
                f"tabla sensible {marca['tabla']}",
                {"tabla": f"{marca['base']}.{marca['tabla']}"},
            )


def columnas_enmascaradas(sesion: Sesion, sensibles: list[dict]) -> set[tuple[str, str, str]]:
    """(base, esquema.tabla, columna) en minúsculas que el rol ve enmascaradas."""
    return {
        (m["base"].lower(), m["tabla"].lower(), c.lower())
        for m in sensibles
        if sesion.rol not in m["roles_ven_columnas"]
        for c in m["columnas"]
    }


def _rige_ahora(regla: dict, conexion: Conexion, ahora: datetime) -> bool:
    if not regla.get("desde"):
        return True
    local = ahora.astimezone(ZoneInfo(conexion.zona_horaria)).time()
    desde, hasta = time.fromisoformat(regla["desde"]), time.fromisoformat(regla["hasta"])
    # Una franja que cruza la medianoche (p. ej. 18:00 a 09:00) se evalúa en dos tramos.
    return desde <= local < hasta if desde < hasta else local >= desde or local < hasta


def _alcanza(regla: dict, conexion: Conexion, base: str, analisis: Analisis) -> bool:
    if regla.get("conexion_id") not in (None, conexion.id):
        return False
    if regla.get("ambientes") and conexion.ambiente not in regla["ambientes"]:
        return False
    if not set(regla["acciones"]) & analisis.acciones:
        return False
    bases = {base.lower()} | {t.base.lower() for t in analisis.tablas}
    if regla.get("base") and regla["base"].lower() not in bases:
        return False
    if regla.get("tablas"):
        return any(
            _misma_tabla(t, t.base, nombre) for t in analisis.tablas for nombre in regla["tablas"]
        )
    return True


async def evaluar_reglas(
    tenant: Tenant,
    conexion: Conexion,
    base: str,
    analisis: Analisis,
    ahora: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    """Reglas que se incumplen, separadas en (bloqueos, advertencias) por prioridad."""
    reglas = await repositorios.listar(
        tenant.base_mongodb, str(tenant.id), {"tipo": "regla", "activa": True}
    )
    momento = ahora or datetime.now(UTC)
    aplicables = [
        {k: r[k] for k in ("id", "descripcion", "prioridad", "efecto")}
        for r in reglas
        if _alcanza(r, conexion, base, analisis) and _rige_ahora(r, conexion, momento)
    ]
    bloqueos = [r for r in aplicables if r["efecto"] == "bloquear"]
    advertencias = [r for r in aplicables if r["efecto"] == "advertir"]
    return bloqueos, advertencias


# --- Contexto para el motor IA --------------------------------------------------------


def _linea_tabla(tabla: dict) -> str:
    columnas = ", ".join(
        f"{c['nombre']} {c['tipo']}{' PK' if c['nombre'] in tabla['pk'] else ''}"
        for c in tabla["columnas"]
    )
    linea = f"- {tabla['esquema']}.{tabla['nombre']}{' (vista)' if tabla['vista'] else ''}"
    linea += f": {columnas}"
    if tabla["fk"]:
        linea += " | FK: " + ", ".join(f"{f['columna']}→{f['referencia']}" for f in tabla["fk"])
    return linea


async def _texto_esquema(tenant: Tenant, conexion: Conexion, base: str | None) -> str:
    """Esquema de la instancia (o de una base) para que la IA relacione el documento."""
    datos_esquema = await esquema.obtener(tenant, conexion.id)
    partes = [f"# Instancia {conexion.alias} (ambiente {conexion.ambiente})"]
    for nombre in sorted(datos_esquema):
        if base is None or nombre == base:
            partes.append(f"\n## Base {nombre}")
            partes += [_linea_tabla(t) for t in datos_esquema[nombre]]
    return "\n".join(partes)


async def texto_para_ia(tenant: Tenant, conexion: Conexion) -> str:
    """Esquema y contexto de negocio de la instancia, en texto estable para la caché."""
    datos_esquema = await esquema.obtener(tenant, conexion.id)
    elementos = await repositorios.listar(
        tenant.base_mongodb,
        str(tenant.id),
        {"conexion_id": {"$in": [None, conexion.id]}, "tipo": {"$in": list(TIPOS)}},
    )
    partes = [f"# Instancia {conexion.alias} (ambiente {conexion.ambiente})"]
    for base in sorted(datos_esquema):
        partes.append(f"\n## Base {base}")
        partes += [_linea_tabla(t) for t in datos_esquema[base]]

    terminos = [e for e in elementos if e["tipo"] == "termino"]
    if terminos:
        partes.append("\n# Glosario de negocio (término → entidad técnica)")
        for t in terminos:
            sinonimos = f" (sinónimos: {', '.join(t['sinonimos'])})" if t["sinonimos"] else ""
            partes.append(
                f"- «{t['termino']}»{sinonimos} → {t['base']}.{t['entidad']}"
                + (f": {t['descripcion']}" if t["descripcion"] else "")
            )
    reglas = [e for e in elementos if e["tipo"] == "regla" and e["activa"]]
    if reglas:
        partes.append("\n# Reglas de negocio")
        partes += [f"- [{r['efecto']}] {r['descripcion']}" for r in reglas]
    sensibles = [e for e in elementos if e["tipo"] == "sensible"]
    if sensibles:
        partes.append("\n# Tablas sensibles (acceso restringido o columnas enmascaradas)")
        partes += [
            f"- {s['base']}.{s['tabla']}"
            + (f" columnas: {', '.join(s['columnas'])}" if s["columnas"] else "")
            for s in sensibles
        ]
    # Resúmenes revisados de los documentos de negocio (no el documento entero).
    documentos_listos = [
        e for e in elementos if e["tipo"] == "documento" and e["estado"] == LISTO and e["resumen"]
    ]
    if documentos_listos:
        partes.append("\n# Documentos de negocio (resúmenes)")
        for d in documentos_listos:
            clase = "funcional" if d["clase"] == "funcional" else "no funcional"
            alcance = f"base {d['base']}" if d["base"] else "todas las bases"
            partes.append(f"\n## {d['nombre']} (documento {clase}, {alcance})\n{d['resumen']}")
    return "\n".join(partes)
