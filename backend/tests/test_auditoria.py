import logging
import queue
import sys
import time
from datetime import UTC, date, datetime

import pytest
from bson import ObjectId

from app.core import contexto, excepciones
from app.core.errores import ErrorDominio
from app.modulos.auditoria import reporte_pdf, repositorios, servicios
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.auditoria.servicios import Filtros
from tests.test_conexiones import (
    CABECERA,
    TENANT,
    _actor,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)

TS = datetime(2026, 10, 20, 17, 32, 39, tzinfo=UTC)


def _transaccion(**detalle):
    return {
        "_id": ObjectId(),
        "tenant_id": str(TENANT.id),
        "accion": detalle.pop("accion", "chat.consulta_ejecutada"),
        "usuario": "dba@acme.com",
        "ip": "192.168.10.42",
        "ts": TS,
        "detalle": detalle,
    }


@pytest.fixture
def bitacoras(entorno, monkeypatch):  # noqa: F811
    datos = {
        "bitacora_transacciones": [
            _transaccion(
                tipo="SELECT",
                conexion="ERP",
                ambiente="PROD",
                base="Ventas",
                sql="SELECT 1",
                total=10,
                estado="exitosa",
            ),
            _transaccion(
                accion="workaround.ejecutado",
                workaround="shrink_log",
                workaround_nombre="Shrink del log",
                conexion_alias="ERP",
                estado="fallida",
            ),
            _transaccion(
                accion="sandbox.promovido",
                conexion="ERP",
                base="Ventas",
                filas=248,
                promocion=True,
                estado="exitosa",
            ),
        ],
        "bitacora_auditoria": [
            {
                "_id": ObjectId(),
                "tenant_id": str(TENANT.id),
                "accion": "usuario.creado",
                "usuario": "admin@acme.com",
                "ip": None,
                "ts": TS,
                "detalle": {"email": "x@acme.com", "en": TS},
            },
        ],
        "bitacora_excepciones": [
            {
                "_id": ObjectId(),
                "tenant": "acme",
                "ts": TS,
                "nivel": "ERROR",
                "modulo": "app.x",
                "codigo": "CON_DB_003",
                "mensaje": "caída",
                "usuario": None,
                "traza": "Traceback...",
            },
        ],
    }
    consultas, eventos, configuracion = [], [], {}

    async def consultar(base, coleccion, filtro, salto, cantidad):
        consultas.append((coleccion, filtro, salto, cantidad))
        filas = datos[coleccion]
        return filas[salto : salto + cantidad], len(filas)

    async def obtener(base, coleccion, filtro):
        return next((d for d in datos[coleccion] if d["_id"] == filtro["_id"]), None)

    async def contar(base, coleccion, filtro):
        return len(datos[coleccion])

    async def iterar(base, coleccion, filtro):
        for d in datos[coleccion]:
            yield d

    async def registrar(base, evento):
        eventos.append(evento)

    async def leer_configuracion(base, clave):
        return configuracion.get(clave)

    async def guardar_configuracion(base, clave, valores):
        configuracion[clave] = valores

    for nombre, funcion in (
        ("consultar", consultar),
        ("obtener", obtener),
        ("contar", contar),
        ("iterar", iterar),
        ("registrar", registrar),
        ("leer_configuracion", leer_configuracion),
        ("guardar_configuracion", guardar_configuracion),
    ):
        monkeypatch.setattr(repositorios, nombre, funcion)
    return {**entorno, "datos": datos, "consultas": consultas, "eventos_auditoria": eventos}


def test_filtros_a_mongo():
    f = Filtros(date(2026, 10, 1), date(2026, 10, 20), "Marina", "PROD", "chat", "clientes")
    filtro = servicios.filtro_mongo(TENANT, "transacciones", f)
    assert filtro["tenant_id"] == str(TENANT.id)
    # Días en hora de Buenos Aires: 1/10 00:00 = 03:00 UTC; hasta inclusive el 20/10.
    assert filtro["ts"] == {
        "$gte": datetime(2026, 10, 1, 3, tzinfo=UTC),
        "$lt": datetime(2026, 10, 21, 3, tzinfo=UTC),
    }
    assert filtro["usuario"] == {"$regex": "Marina", "$options": "i"}
    assert filtro["detalle.ambiente"] == "PROD" and filtro["accion"] == {"$regex": "^chat\\."}
    assert {"detalle.sql": {"$regex": "clientes", "$options": "i"}} in filtro["$or"]

    excepcion = servicios.filtro_mongo(TENANT, "excepciones", Filtros(tipo="error", texto="a.b"))
    assert excepcion["tenant"] == "acme" and excepcion["nivel"] == "ERROR"
    assert {"mensaje": {"$regex": "a\\.b", "$options": "i"}} in excepcion["$or"]
    with pytest.raises(ErrorDominio):
        servicios.filtro_mongo(TENANT, "transacciones", Filtros(ambiente="QA"))


async def test_listar_y_detalle(bitacoras):
    r = await servicios.listar(TENANT, _actor("DBA"), "transacciones", Filtros(), 1)
    assert r["total"] == 3 and r["paginas"] == 1
    chat, workaround, promocion = r["registros"]
    assert (chat["accion"], chat["entidad"], chat["filas"], chat["ip"]) == (
        "SELECT (chat)",
        "ERP · Ventas",
        10,
        "192.168.10.42",
    )
    assert chat["fecha"] == "2026-10-20 14:32:39"  # hora de Buenos Aires
    assert workaround["accion"] == "WORKAROUND shrink_log" and workaround["resultado"] == "fallida"
    assert workaround["entidad"] == "ERP · Shrink del log"
    assert promocion["promocion"] is True and promocion["filas"] == 248

    detalle = await servicios.detalle(TENANT, _actor("DBA"), "transacciones", chat["id"])
    assert detalle["documento"]["detalle"]["sql"] == "SELECT 1"
    for registro_id in ("no-es-id", str(ObjectId())):
        with pytest.raises(ErrorDominio):
            await servicios.detalle(TENANT, _actor("DBA"), "transacciones", registro_id)

    # La bitácora del sistema es solo para el Administrador (CU-000-009-B).
    with pytest.raises(ErrorDominio) as error:
        await servicios.listar(TENANT, _actor("DBA"), "sistema", Filtros(), 1)
    assert error.value.codigo == "USR_PER_000"
    sistema = await servicios.listar(TENANT, _actor("ADMINISTRADOR"), "sistema", Filtros(), 1)
    assert '"email": "x@acme.com"' in sistema["registros"][0]["resumen"]
    assert servicios.bitacoras_visibles(_actor("DBA")) == ["transacciones", "excepciones"]
    assert await servicios.totales(TENANT, _actor("ADMINISTRADOR")) == {
        "transacciones": 3,
        "sistema": 1,
        "excepciones": 1,
    }
    with pytest.raises(ErrorDominio):
        await servicios.listar(TENANT, _actor("DBA"), "otra", Filtros(), 1)


async def test_exportar_csv_y_pdf(bitacoras, monkeypatch):
    lineas = await servicios.exportar_csv(
        TENANT, _actor("DBA"), "transacciones", Filtros(usuario="dba")
    )
    contenido = "".join([parte async for parte in lineas])
    assert contenido.startswith("﻿Fecha,Usuario,IP,Acción,Ambiente,Entidad,Filas,Resultado,T-SQL")
    assert "SELECT (chat)" in contenido and contenido.count("\r\n") == 4
    evento = bitacoras["eventos_auditoria"][-1]
    assert evento.accion == "auditoria.exportado"
    assert evento.detalle == {
        "bitacora": "transacciones",
        "formato": "csv",
        "filas": 3,
        "filtros": {"usuario": "dba"},
    }

    pdf = await servicios.exportar_pdf(TENANT, _actor("DBA"), "excepciones", Filtros())
    assert (
        pdf.startswith(b"%PDF") and bitacoras["eventos_auditoria"][-1].detalle["formato"] == "pdf"
    )

    monkeypatch.setitem(servicios.LIMITES_EXPORTACION, "pdf", 2)
    with pytest.raises(ErrorDominio) as error:
        await servicios.exportar_pdf(TENANT, _actor("DBA"), "transacciones", Filtros())
    assert error.value.codigo == "EXP_LIM_000" and error.value.datos == {
        "registros": 3,
        "limite": 2,
    }
    with pytest.raises(ErrorDominio):
        await servicios.preparar_exportacion(
            TENANT, _actor("DBA"), "transacciones", Filtros(), "xls"
        )


def test_reporte_pdf_con_textos_largos_y_caracteres_fuera_de_latin1():
    pdf = reporte_pdf.generar(
        "Bitácora",
        "ACME",
        "dba@acme.com",
        {"usuario": "dba"},
        (("Fecha", "fecha"), ("T-SQL", "sql")),
        [{"fecha": "2026-10-20", "sql": "SELECT '😀' " + "x" * 500}],
    )
    assert pdf.startswith(b"%PDF")


async def test_nivel_de_excepciones(bitacoras):
    assert await servicios.nivel_excepciones(TENANT) == "WARNING"
    with pytest.raises(ErrorDominio) as error:
        await servicios.cambiar_nivel_excepciones(TENANT, _actor("DBA"), "ERROR")
    assert error.value.codigo == "USR_PER_000"
    with pytest.raises(ErrorDominio):
        await servicios.cambiar_nivel_excepciones(TENANT, _actor("ADMINISTRADOR"), "TODO")
    assert (
        await servicios.cambiar_nivel_excepciones(TENANT, _actor("ADMINISTRADOR"), "error")
        == "ERROR"
    )
    assert await servicios.nivel_excepciones(TENANT) == "ERROR"
    assert excepciones.nivel_de("acme") == logging.ERROR
    assert bitacoras["eventos_auditoria"][-1].detalle == {"anterior": "WARNING", "nuevo": "ERROR"}
    excepciones._niveles.clear()


async def test_api_auditoria(bitacoras):
    admin = await _navegador(bitacoras, "ADMINISTRADOR")
    r = admin.get("/api/auditoria").json()
    assert r["bitacoras"] == ["transacciones", "sistema", "excepciones"]
    assert r["puede_configurar_nivel"] is True
    pagina = admin.get("/api/auditoria/transacciones?pagina=1&usuario=dba&desde=2026-10-01").json()
    assert pagina["total"] == 3
    assert bitacoras["consultas"][-1][1]["usuario"]["$regex"] == "dba"
    registro = pagina["registros"][0]["id"]
    assert admin.get(f"/api/auditoria/transacciones/registros/{registro}").status_code == 200
    csv = admin.get("/api/auditoria/sistema/exportar?formato=csv")
    assert csv.status_code == 200 and "usuario.creado" in csv.text
    pdf = admin.get("/api/auditoria/excepciones/exportar?formato=pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert admin.put(
        "/api/auditoria/excepciones/nivel", json={"nivel": "INFO"}, headers=CABECERA
    ).json() == {"nivel": "INFO"}
    excepciones._niveles.clear()

    consulta = await _navegador(bitacoras, "CONSULTA")
    assert consulta.get("/api/auditoria").status_code == 403
    assert "Bitácora" in admin.get("/auditoria").text


# --- Bitácora de excepciones (handler de logging) -----------------------------------


def _registro(nivel, mensaje, exc_info=None):
    return logging.LogRecord("app.modulo", nivel, __file__, 1, mensaje, None, exc_info)


def test_documento_de_excepcion():
    contexto.ruta.set("/api/x")
    try:
        raise ValueError("falló")
    except ValueError:
        registro = _registro(logging.ERROR, "CON_DB_003 ERP: timeout", sys.exc_info())
    doc = excepciones.documento(registro, "acme", "dba@acme.com")
    assert doc["codigo"] == "CON_DB_003" and doc["nivel"] == "ERROR" and doc["ruta"] == "/api/x"
    assert "ValueError: falló" in doc["traza"] and doc["usuario"] == "dba@acme.com"
    assert (
        excepciones.documento(_registro(logging.WARNING, "WRM_000 x"), None, None)["codigo"]
        == "WRM_000"
    )


def test_manejador_filtra_por_nivel_del_tenant():
    manejador = excepciones.ManejadorExcepciones()
    token = contexto.tenant_slug.set("acme")
    try:
        manejador.emit(_registro(logging.INFO, "detalle"))
        manejador.emit(_registro(logging.WARNING, "aviso"))
        excepciones.actualizar_nivel("acme", "DEBUG")
        manejador.emit(_registro(logging.DEBUG, "fino"))
    finally:
        contexto.tenant_slug.reset(token)
        excepciones._niveles.clear()
    mensajes = [manejador.cola.get_nowait()["mensaje"] for _ in range(manejador.cola.qsize())]
    assert mensajes == ["aviso", "fino"]
    manejador.cola = queue.Queue(1)
    manejador.emit(_registro(logging.ERROR, "uno"))
    manejador.emit(_registro(logging.ERROR, "dos"))  # cola llena: se descarta
    assert manejador.cola.qsize() == 1


def test_escritor_guarda_en_la_base_del_tenant(monkeypatch):
    guardados = []

    class Coleccion:
        def __init__(self, base, nombre):
            self.base, self.nombre = base, nombre

        def insert_one(self, doc):
            guardados.append((self.base, self.nombre, doc["mensaje"]))

        def find_one(self, filtro):
            return {"nivel": "ERROR"} if self.base == "db_t_acme" else None

    class Cliente:
        def __getitem__(self, base):
            return _Base(base)

        def close(self):
            pass

    class _Base:
        def __init__(self, base):
            self.base = base

        def __getitem__(self, nombre):
            return Coleccion(self.base, nombre)

    manejador = excepciones.ManejadorExcepciones()
    escritor = excepciones.Escritor(manejador, lambda: {"acme": "db_t_acme"}, "plataforma")
    monkeypatch.setattr(escritor, "_conectar", lambda: Cliente())
    manejador.cola.put({"tenant": "acme", "mensaje": "uno"})
    manejador.cola.put({"tenant": None, "mensaje": "dos"})
    escritor.iniciar()
    for _ in range(100):
        if len(guardados) == 2:
            break
        time.sleep(0.02)
    escritor.detener.set()
    assert guardados[0] == ("db_t_acme", "bitacora_excepciones", "uno")
    assert guardados[1] == ("plataforma", "bitacora_excepciones", "dos")
    assert excepciones.nivel_de("acme") == logging.ERROR
    excepciones._niveles.clear()


def test_ip_de_la_peticion_en_los_eventos():
    token = contexto.ip.set("10.0.0.7")
    try:
        assert EventoAuditoria("x.y", "u", "t").ip == "10.0.0.7"
    finally:
        contexto.ip.reset(token)
    assert EventoAuditoria("x.y", "u", "t").ip is None
