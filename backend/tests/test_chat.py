import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import anthropic
import httpx2
import pyodbc
import pytest

from app.core.errores import ErrorDominio
from app.identidad.entidades import Usuario
from app.modulos.chat_ia import analizador, ejecucion, motor_ia, repositorios, servicios
from app.modulos.chat_ia.analizador import Tabla
from app.modulos.chat_ia.motor_ia import Traduccion
from app.modulos.conexiones import servicios as conexiones
from app.modulos.contexto_negocio import esquema
from app.modulos.contexto_negocio import servicios as contexto
from app.modulos.contexto_negocio.entidades import Regla, TablaSensible
from tests import contexto_falso
from tests.test_conexiones import (
    CABECERA,
    TENANT,
    _actor,
    _datos,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)
from tests.test_contexto import ESQUEMA


def _hilos_en_memoria(monkeypatch) -> dict:
    hilos: dict[str, dict] = {}

    async def crear(base, tenant_id, usuario_id, conexion_id, titulo):
        hilo_id = f"{len(hilos) + 1:024x}"
        ahora = datetime.now(UTC).isoformat()
        hilos[hilo_id] = {
            "id": hilo_id,
            "usuario_id": usuario_id,
            "conexion_id": conexion_id,
            "titulo": titulo,
            "mensajes": [],
            "creado_en": ahora,
            "actualizado_en": ahora,
        }
        return hilo_id

    async def obtener(base, tenant_id, usuario_id, hilo_id):
        hilo = hilos.get(hilo_id)
        return hilo if hilo and hilo["usuario_id"] == usuario_id else None

    async def listar(base, tenant_id, usuario_id):
        return [h for h in hilos.values() if h["usuario_id"] == usuario_id]

    async def agregar_mensajes(base, hilo_id, mensajes):
        hilos[hilo_id]["mensajes"] += mensajes

    async def actualizar_mensaje(base, hilo_id, mensaje_id, campos):
        for m in hilos[hilo_id]["mensajes"]:
            if m["id"] == mensaje_id:
                m.update(campos)

    async def eliminar(base, tenant_id, usuario_id, hilo_id):
        return hilos.pop(hilo_id, None) is not None

    for nombre, funcion in (
        ("crear", crear),
        ("obtener", obtener),
        ("listar", listar),
        ("agregar_mensajes", agregar_mensajes),
        ("actualizar_mensaje", actualizar_mensaje),
        ("eliminar", eliminar),
    ):
        monkeypatch.setattr(repositorios, nombre, funcion)
    return hilos


@pytest.fixture
def chat(entorno, monkeypatch):  # noqa: F811
    contexto_falso.instalar(monkeypatch)
    hilos = _hilos_en_memoria(monkeypatch)
    entorno["usuarios"].usuarios["u"] = Usuario(
        "u", str(TENANT.id), "dba@acme.com", "Dba", "DBA", "activo", None, ("DEV", "PROD")
    )

    async def obtener_esquema(tenant, conexion_id, refrescar=False):
        return ESQUEMA

    respuestas = []
    pedidos = []

    async def traducir(texto_contexto, historial, pedido):
        pedidos.append((texto_contexto, historial, pedido))
        return respuestas.pop(0)

    transacciones = []

    async def registrar_transaccion(base, evento):
        transacciones.append((evento.accion, evento.detalle))

    lecturas, escrituras, ocupadas = [], [], []

    def leer(conexion, base, sql, limite, enmascaradas):
        lecturas.append((base, sql, limite, enmascaradas))
        return {
            "columnas": ["id", "cuit"],
            "filas": [[1, "****"]],
            "total": 5000,
            "enmascaradas": [1],
            "duracion_ms": 12,
        }

    def escribir(conexion, base, sql):
        escrituras.append((base, sql))
        if "fallar" in sql:
            raise ErrorDominio("SQL_EXE_000", "error 1222: lock timeout")
        return {"filas_afectadas": 3, "duracion_ms": 5}

    def iterar(conexion, base, sql, enmascaradas):
        yield ["id", "cuit"]
        yield [1, "****"]
        yield [2, None]

    monkeypatch.setattr(esquema, "obtener", obtener_esquema)
    monkeypatch.setattr(motor_ia, "traducir", traducir)
    monkeypatch.setattr(servicios.auditoria, "registrar_transaccion", registrar_transaccion)
    monkeypatch.setattr(ejecucion, "leer", leer)
    monkeypatch.setattr(ejecucion, "escribir", escribir)
    monkeypatch.setattr(ejecucion, "bloqueos", lambda c, b, tablas: list(ocupadas))
    monkeypatch.setattr(ejecucion, "iterar", iterar)
    conteos = []

    def contar(conexion, base, consulta):
        conteos.append((base, consulta))
        return 3

    monkeypatch.setattr(ejecucion, "contar", contar)
    return {
        "conteos": conteos,
        **entorno,
        "hilos": hilos,
        "respuestas": respuestas,
        "pedidos": pedidos,
        "transacciones": transacciones,
        "lecturas": lecturas,
        "escrituras": escrituras,
        "ocupadas": ocupadas,
    }


def _traduccion(sql, base="Ventas", confianza=90):
    return Traduccion(sql=sql, base=base, explicacion="Listo.", confianza=confianza)


async def _hilo_con(chat, conexion, sql, actor=None):
    chat["respuestas"].append(_traduccion(sql))
    r = await servicios.enviar(TENANT, actor or _actor(), None, conexion.id, "pedido")
    return r["hilo_id"], r["respuesta"]


# --- Conversación -------------------------------------------------------------------


async def test_enviar_y_seguimiento(chat):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    chat["respuestas"].append(_traduccion("SELECT id FROM dbo.clientes", base="VENTAS"))
    r = await servicios.enviar(TENANT, _actor(), None, conexion.id, "  clientes  ")
    respuesta = r["respuesta"]
    assert respuesta["base"] == "Ventas" and respuesta["revision"]["directo"] is True
    assert (
        respuesta["revision"]["tipo"] == "SELECT"
        and respuesta["revision"]["segundo_factor"] is False
    )
    assert "## Base Ventas" in chat["pedidos"][0][0] and chat["pedidos"][0][2] == "clientes"
    # La lectura se ejecuta sola y el resultado viene con la respuesta.
    assert r["resultado"]["filas"] == [[1, "****"]] and respuesta["ejecucion"]["total"] == 5000
    assert chat["lecturas"][-1][1] == "SELECT id FROM dbo.clientes"

    # Seguimiento: el motor IA recibe los turnos anteriores.
    chat["respuestas"].append(
        Traduccion(sql=None, base=None, explicacion="¿Qué mes?", confianza=20)
    )
    r2 = await servicios.enviar(TENANT, _actor(), r["hilo_id"], None, "y del mes pasado")
    assert r2["respuesta"]["sql"] is None and r2["respuesta"]["revision"] is None
    historial = chat["pedidos"][1][1]
    assert historial[0] == {"role": "user", "content": "clientes"}
    assert historial[1]["role"] == "assistant" and "SELECT id" in historial[1]["content"]

    # Base inexistente: se informa en la revisión.
    chat["respuestas"].append(_traduccion("SELECT 1", base="Inventada"))
    r3 = await servicios.enviar(TENANT, _actor(), r["hilo_id"], None, "otra")
    assert "error" in r3["respuesta"]["revision"]

    assert [h["id"] for h in await servicios.hilos(TENANT, _actor())] == [r["hilo_id"]]
    with pytest.raises(ErrorDominio) as error:
        await servicios.hilo(TENANT, _actor(), "ajeno")
    assert error.value.codigo == "CHT_VAL_000"
    for hilo_id, conexion_id, texto, codigo in (
        (None, conexion.id, " ", "USR_VAL_002"),
        (None, None, "x", "USR_VAL_002"),
    ):
        with pytest.raises(ErrorDominio) as error:
            await servicios.enviar(TENANT, _actor(), hilo_id, conexion_id, texto)
        assert error.value.codigo == codigo
    await servicios.eliminar_hilo(TENANT, _actor(), r["hilo_id"])
    with pytest.raises(ErrorDominio):
        await servicios.eliminar_hilo(TENANT, _actor(), r["hilo_id"])


async def test_ambiente_no_habilitado(chat):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="TESTING"))
    assert await servicios.conexiones_disponibles(TENANT, _actor()) == []
    with pytest.raises(ErrorDominio) as error:
        await servicios.enviar(TENANT, _actor(), None, conexion.id, "x")
    assert error.value.codigo == "USR_PER_000"


async def test_revision_segun_rol_y_sentencia(chat):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    casos = (
        (_actor(), "ALTER TABLE dbo.clientes ADD x INT", "sandbox"),
        (_actor(), "UPDATE dbo.clientes SET estado = 1", "falta_where"),
        (_actor("CONSULTA"), "UPDATE dbo.clientes SET estado = 1 WHERE id = 1", "solo_lectura"),
        (_actor(), "DELETE FROM dbo.clientes WHERE id = 1", None),
    )
    for actor, sql, motivo in casos:
        revision = await servicios.revisar(TENANT, actor, conexion, "Ventas", sql)
        assert revision["motivo"] == motivo
    revision = await servicios.revisar(
        TENANT, _actor(), conexion, "Ventas", "DELETE FROM dbo.clientes WHERE id = 1"
    )
    assert revision["segundo_factor"] is True and revision["destructiva"] is True
    # Lo que se va a modificar, armado desde el T-SQL, con las filas que cumplen hoy la condición.
    assert revision["modificacion"] == {
        "operacion": "DELETE",
        "tabla": "dbo.clientes",
        "cambios": [],
        "condicion": "id = 1",
        "filas": None,
        "desde_consulta": False,
        "filas_estimadas": 3,
    }
    assert chat["conteos"][-1] == ("Ventas", "SELECT COUNT_BIG(*) FROM dbo.clientes WHERE id = 1")
    # Sin WHERE no se puede ejecutar: no se cuenta.
    conteos = len(chat["conteos"])
    sin_where = await servicios.revisar(
        TENANT, _actor(), conexion, "Ventas", "UPDATE dbo.clientes SET estado = 1"
    )
    assert "filas_estimadas" not in sin_where["modificacion"] and len(chat["conteos"]) == conteos
    assert (await servicios.revisar(TENANT, _actor(), conexion, "Ventas", "SELECT 1"))[
        "modificacion"
    ] is None
    assert (await servicios.revisar(TENANT, _actor("CONSULTA"), conexion, "Ventas", "SELECT 1"))[
        "puede_sandbox"
    ] is False

    await contexto.guardar_sensible(
        TENANT,
        _actor(),
        None,
        TablaSensible(conexion.id, "Ventas", "dbo.clientes", (), ("DBA",), (), False),
    )
    revision = await servicios.revisar(
        TENANT, _actor("ADMINISTRADOR"), conexion, "Ventas", "SELECT * FROM dbo.clientes"
    )
    assert revision["motivo"] == "tabla_restringida"
    await contexto.guardar_regla(
        TENANT,
        _actor(),
        None,
        Regla("Nada de pedidos", ("SELECT",), "bloquear", tablas=("dbo.pedidos",)),
    )
    revision = await servicios.revisar(
        TENANT, _actor(), conexion, "Ventas", "SELECT * FROM dbo.pedidos"
    )
    assert (
        revision["motivo"] == "regla"
        and revision["reglas_bloqueo"][0]["descripcion"] == "Nada de pedidos"
    )

    hilo_id, _ = await _hilo_con(chat, conexion, "SELECT 1")
    editada = await servicios.revisar_edicion(
        TENANT, _actor(), hilo_id, "Ventas", "DROP TABLE dbo.x"
    )
    assert editada["tipo"] == "SANDBOX" and editada["destructiva"] is True


async def test_lectura_automatica_falla_o_espera_confirmacion(chat, monkeypatch):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))

    def fallar(*argumentos):
        raise ErrorDominio("SQL_EXE_000", "Invalid column name 'x'")

    monkeypatch.setattr(ejecucion, "leer", fallar)
    chat["respuestas"].append(_traduccion("SELECT x FROM dbo.clientes"))
    r = await servicios.enviar(TENANT, _actor(), None, conexion.id, "pedido")
    assert r["resultado"] is None and r["respuesta"]["ejecucion"] is None
    assert r["respuesta"]["error_ejecucion"]["codigo"] == "SQL_EXE_000"

    # Una regla que advierte deja la lectura a confirmación del usuario.
    await contexto.guardar_regla(
        TENANT,
        _actor(),
        None,
        Regla("Cuidado con pedidos", ("SELECT",), "advertir", tablas=("dbo.pedidos",)),
    )
    lecturas = len(chat["lecturas"])
    chat["respuestas"].append(_traduccion("SELECT * FROM dbo.pedidos"))
    r = await servicios.enviar(TENANT, _actor(), None, conexion.id, "pedido")
    assert r["resultado"] is None and "error_ejecucion" not in r["respuesta"]
    assert len(chat["lecturas"]) == lecturas


# --- Ejecución ----------------------------------------------------------------------


async def test_ejecutar_select_y_exportar(chat):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="PROD"))
    await contexto.guardar_sensible(
        TENANT,
        _actor(),
        None,
        TablaSensible(
            conexion.id, "Ventas", "dbo.clientes", ("cuit",), ("DBA", "CONSULTA"), (), False
        ),
    )
    hilo_id, m = await _hilo_con(chat, conexion, "SELECT id, cuit FROM dbo.clientes")
    assert m["ejecucion"]["tipo"] == "SELECT"

    # Solo se exportan lecturas ejecutadas.
    chat["respuestas"].append(_traduccion("UPDATE dbo.clientes SET a = 1 WHERE id = 1"))
    sin_ejecutar = (await servicios.enviar(TENANT, _actor(), hilo_id, None, "pedido"))["respuesta"]
    assert sin_ejecutar["ejecucion"] is None
    with pytest.raises(ErrorDominio) as error:
        await servicios.exportar(TENANT, _actor(), hilo_id, sin_ejecutar["id"])
    assert error.value.codigo == "USR_VAL_002"

    # Volver a ejecutar: en PROD un SELECT no pide segundo factor.
    r = await servicios.ejecutar(TENANT, _actor(), hilo_id, m["id"], m["sql"], "Ventas")
    assert r["total"] == 5000 and r["ejecucion"]["filas"] == 1 and r["ejecucion"]["total"] == 5000
    base, _, limite, enmascaradas = chat["lecturas"][-1]
    assert limite == 1000 and enmascaradas == {("ventas", "dbo.clientes", "cuit")}
    accion, detalle = chat["transacciones"][-1]
    assert accion == "chat.consulta_ejecutada" and detalle["editada"] is False
    assert chat["hilos"][hilo_id]["mensajes"][1]["ejecucion"]["tipo"] == "SELECT"

    filas, auditar = await servicios.exportar(TENANT, _actor(), hilo_id, m["id"])
    assert list(filas) == [["id", "cuit"], [1, "****"], [2, None]]
    await auditar(2)
    assert chat["transacciones"][-1] == (
        "chat.exportado",
        {
            "conexion": conexion.alias,
            "ambiente": "PROD",
            "base": "Ventas",
            "sql": m["sql"],
            "filas": 2,
            "formato": "csv",
        },
    )
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(TENANT, _actor(), hilo_id, "otro", "SELECT 1", "Ventas")
    assert error.value.codigo == "CHT_VAL_000"


async def test_politica_de_escritura(chat):
    prod = await conexiones.crear(TENANT, _actor(), _datos(ambiente="PROD"))
    dev = await conexiones.crear(TENANT, _actor(), _datos(alias="DEV-01", ambiente="DEV"))
    con_factor = _actor(segundo_factor_en=time.time())
    hilo_prod, m_prod = await _hilo_con(
        chat, prod, "UPDATE dbo.clientes SET estado = 1 WHERE id = 1"
    )
    hilo_dev, m_dev = await _hilo_con(chat, dev, "UPDATE dbo.clientes SET estado = 1 WHERE id = 1")

    async def intentar(actor, hilo_id, m, sql, confirmar=False):
        with pytest.raises(ErrorDominio) as error:
            await servicios.ejecutar(TENANT, actor, hilo_id, m["id"], sql, "Ventas", confirmar)
        return error.value

    assert (
        await intentar(_actor(), hilo_dev, m_dev, "ALTER TABLE dbo.t ADD b INT")
    ).codigo == "SQL_VAL_001"
    assert (
        await intentar(_actor(), hilo_dev, m_dev, "UPDATE dbo.t SET a = 1")
    ).codigo == "SQL_VAL_002"
    assert (
        await intentar(_actor("CONSULTA"), hilo_dev, m_dev, m_dev["sql"])
    ).codigo == "USR_PER_000"
    assert (await intentar(_actor(), hilo_prod, m_prod, m_prod["sql"])).codigo == "AMB_VAL_000"
    delete = "DELETE FROM dbo.clientes WHERE id = 1"
    assert (await intentar(_actor(), hilo_dev, m_dev, delete)).codigo == "AMB_VAL_000"

    # UPDATE en DEV: directo, sin segundo factor.
    r = await servicios.ejecutar(TENANT, _actor(), hilo_dev, m_dev["id"], m_dev["sql"], "Ventas")
    assert r["filas_afectadas"] == 3 and r["tipo"] == "UPDATE"
    assert chat["escrituras"][-1] == ("Ventas", m_dev["sql"])

    # Bloqueos o transacciones abiertas sobre la tabla: no se ejecuta.
    chat["ocupadas"].append({"sesion": 57, "usuario": "app", "tablas": ["Ventas.dbo.clientes"]})
    error = await intentar(_actor(), hilo_dev, m_dev, m_dev["sql"])
    assert error.codigo == "SQL_LCK_000" and error.datos["sesiones"][0]["sesion"] == 57
    chat["ocupadas"].clear()

    # Tabla sensible con doble confirmación.
    await contexto.guardar_sensible(
        TENANT,
        _actor(),
        None,
        TablaSensible(
            dev.id, "Ventas", "dbo.clientes", (), ("DBA", "ADMINISTRADOR", "CONSULTA"), (), True
        ),
    )
    assert (await intentar(_actor(), hilo_dev, m_dev, m_dev["sql"])).codigo == "CTX_VAL_001"
    assert (
        await servicios.ejecutar(TENANT, con_factor, hilo_dev, m_dev["id"], m_dev["sql"], "Ventas")
    )["filas_afectadas"] == 3

    # Reglas: advertir pide confirmación; bloquear impide.
    await contexto.guardar_regla(TENANT, _actor(), None, Regla("Avisá", ("UPDATE",), "advertir"))
    error = await intentar(con_factor, hilo_dev, m_dev, m_dev["sql"])
    assert error.codigo == "CTX_REG_001" and error.datos["reglas"][0]["descripcion"] == "Avisá"
    await servicios.ejecutar(
        TENANT, con_factor, hilo_dev, m_dev["id"], m_dev["sql"], "Ventas", True
    )
    regla = await contexto.guardar_regla(
        TENANT, _actor(), None, Regla("No", ("UPDATE",), "bloquear")
    )
    assert (await intentar(con_factor, hilo_dev, m_dev, m_dev["sql"], True)).codigo == "CTX_REG_000"
    await contexto.eliminar(TENANT, _actor(), "regla", regla["id"])

    # Falla en la instancia: se revierte, se informa y queda en la bitácora.
    fallida = "UPDATE dbo.clientes SET estado = 1 WHERE id = 1 -- fallar"
    assert (await intentar(con_factor, hilo_dev, m_dev, fallida, True)).codigo == "SQL_EXE_000"
    assert chat["transacciones"][-1][1]["estado"] == "fallida"
    assert chat["transacciones"][-1][1]["editada"] is True


async def test_validar_en_sandbox(chat, monkeypatch):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    hilo_id, m = await _hilo_con(chat, conexion, "ALTER TABLE dbo.clientes ADD x INT")
    llamadas = []

    async def ejecutar_sandbox(tenant, actor, conexion_id, base, sql, solo_sintaxis):
        llamadas.append((conexion_id, base, sql, solo_sintaxis))
        return {"id": "r1", "estado": "ok", "filas": 0, "duracion_ms": 9, "avisos": []}

    monkeypatch.setattr(servicios.sandbox, "ejecutar", ejecutar_sandbox)
    resumen = await servicios.validar_en_sandbox(
        TENANT, _actor(), hilo_id, m["id"], m["sql"], "Ventas"
    )
    assert resumen["registro_id"] == "r1" and llamadas == [(conexion.id, "Ventas", m["sql"], False)]
    assert chat["hilos"][hilo_id]["mensajes"][1]["sandbox"]["estado"] == "ok"
    with pytest.raises(ErrorDominio) as error:
        await servicios.validar_en_sandbox(
            TENANT, _actor("CONSULTA"), hilo_id, m["id"], m["sql"], "Ventas"
        )
    assert error.value.codigo == "USR_PER_000"


# --- Motor IA -----------------------------------------------------------------------


class _Mensajes:
    def __init__(self, resultado):
        self.resultado = resultado
        self.llamadas = []

    async def parse(self, **kwargs):
        self.llamadas.append(kwargs)
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


def _cliente_falso(mensajes):
    cliente = SimpleNamespace(messages=mensajes)
    cliente.with_options = lambda **opciones: cliente
    return cliente


def _respuesta(parsed, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=parsed,
        usage=SimpleNamespace(input_tokens=10, cache_read_input_tokens=5, output_tokens=3),
    )


async def test_motor_ia(monkeypatch, redis_falso):
    traduccion = _traduccion("SELECT 1")
    mensajes = _Mensajes(_respuesta(traduccion))
    monkeypatch.setattr(motor_ia, "_cliente", lambda: _cliente_falso(mensajes))

    historial = [{"role": "user", "content": "antes"}]
    assert await motor_ia.traducir("ctx", historial, "pedido") == traduccion
    assert await motor_ia.traducir("ctx", historial, "pedido") == traduccion  # desde la caché
    assert len(mensajes.llamadas) == 1
    llamada = mensajes.llamadas[0]
    assert llamada["model"] == "claude-haiku-5-5" and llamada["output_format"] is Traduccion
    assert llamada["system"][1] == {
        "type": "text",
        "text": "ctx",
        "cache_control": {"type": "ephemeral"},
    }
    assert llamada["messages"][-1] == {"role": "user", "content": "pedido"}
    assert llamada["output_config"] == {"effort": "low"}

    solicitud = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    errores = (
        (anthropic.APIConnectionError(request=solicitud), "IA_API_000"),
        (
            anthropic.RateLimitError(
                "límite", response=httpx2.Response(429, request=solicitud), body=None
            ),
            "IA_API_000",
        ),
        (
            anthropic.InternalServerError(
                "caído", response=httpx2.Response(500, request=solicitud), body=None
            ),
            "IA_API_000",
        ),
        (_respuesta(None, "refusal"), "IA_TRA_000"),
    )
    for resultado, codigo in errores:
        monkeypatch.setattr(motor_ia, "_cliente", lambda r=resultado: _cliente_falso(_Mensajes(r)))
        with pytest.raises(ErrorDominio) as error:
            await motor_ia.traducir("ctx", [], f"otro {codigo} {id(resultado)}")
        assert error.value.codigo == codigo


def test_cliente_sin_clave(monkeypatch):
    monkeypatch.delenv("SECRETO_ANTHROPIC_API_KEY", raising=False)
    motor_ia._cliente.cache_clear()
    with pytest.raises(ErrorDominio) as error:
        motor_ia._cliente()
    assert error.value.codigo == "IA_API_000"
    monkeypatch.setenv("SECRETO_ANTHROPIC_API_KEY", "clave-de-prueba")
    motor_ia._cliente.cache_clear()
    assert isinstance(motor_ia._cliente(), anthropic.AsyncAnthropic)
    motor_ia._cliente.cache_clear()


# --- Ejecución sobre la instancia (pyodbc simulado) ---------------------------------


class _Cursor:
    def __init__(self, conexion):
        self.conexion = conexion
        self.description = None
        self.filas = []
        self.rowcount = -1

    def execute(self, sql, *params):
        self.conexion.ejecutadas.append(sql)
        for clave, respuesta in self.conexion.respuestas.items():
            if clave in sql:
                if isinstance(respuesta, Exception):
                    raise respuesta
                self.description, self.filas, self.rowcount = respuesta
                return self
        self.description, self.filas = None, []
        return self

    def fetchall(self):
        return self.filas

    def fetchone(self):
        return self.filas[0] if self.filas else None

    def fetchmany(self, cantidad):
        lote, self.filas = self.filas[:cantidad], self.filas[cantidad:]
        return lote


class _ConexionSql:
    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.ejecutadas = []
        self.autocommit = True
        self.timeout = 0
        self.estado = []

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.estado.append("commit")

    def rollback(self):
        self.estado.append("rollback")

    def close(self):
        self.estado.append("close")


def _origen(nombre, base, esquema_, tabla, columna, oculta=False):
    return SimpleNamespace(
        name=nombre,
        source_database=base,
        source_schema=esquema_,
        source_table=tabla,
        source_column=columna,
        is_hidden=oculta,
    )


def test_leer_enmascara_y_cuenta(monkeypatch):
    descripcion = [("id",), ("dato",), ("calculo",)]
    origen = [
        _origen("id", "Ventas", "dbo", "clientes", "id"),
        _origen("dato", "Ventas", "dbo", "clientes", "cuit"),
        _origen("calculo", None, None, None, None),
        _origen("oculta", "Ventas", "dbo", "clientes", "x", oculta=True),
    ]
    filas = [(i, f"2033{i}", Decimal("1.5")) for i in range(7)]
    real = _ConexionSql(
        {
            "sp_describe_first_result_set": (None, origen, 0),
            "SELECT": (descripcion, filas, -1),
        }
    )
    monkeypatch.setattr(ejecucion.sqlserver, "abrir_externo", lambda *a, **k: real)
    monkeypatch.setattr(ejecucion.conexiones, "servidor_de", lambda c: "h")
    monkeypatch.setattr(ejecucion.cifrado, "descifrar", lambda p: "x")
    monkeypatch.setattr(ejecucion, "TAMANO_LECTURA", 2)
    conexion = SimpleNamespace(usuario_sql="u", password_sql_cifrada="p")
    sql = "SELECT id, cuit AS dato, LEFT(cuit, 2) AS calculo FROM dbo.clientes"
    mascara = {("ventas", "dbo.clientes", "cuit")}

    r = ejecucion.leer(conexion, "Ventas", sql, 3, mascara)
    assert r["columnas"] == ["id", "dato", "calculo"] and r["total"] == 7
    assert r["filas"][0] == [0, "****", "****"] and r["enmascaradas"] == [1, 2]
    assert real.estado[-2:] == ["rollback", "close"] and real.timeout == 60

    sin_mascara = ejecucion.leer(conexion, "Ventas", sql, 3, set())
    assert sin_mascara["filas"][0] == [0, "20330", "1.5"]

    assert list(ejecucion.iterar(conexion, "Ventas", sql, mascara))[:2] == [
        ["id", "dato", "calculo"],
        [0, "****", "****"],
    ]

    real.respuestas["sp_describe_first_result_set"] = pyodbc.ProgrammingError("42000", "temp")
    with pytest.raises(ErrorDominio) as error:
        ejecucion.leer(conexion, "Ventas", sql, 3, mascara)
    assert error.value.codigo == "USR_PER_000"
    real.respuestas["SELECT"] = pyodbc.ProgrammingError("42S02", "Invalid object name")
    with pytest.raises(ErrorDominio) as error:
        ejecucion.leer(conexion, "Ventas", sql, 3, set())
    assert error.value.codigo == "SQL_EXE_000"


def test_escribir_y_bloqueos(monkeypatch):
    real = _ConexionSql(
        {"UPDATE": (None, [], 4), "dm_tran_locks": (None, [(57, "app", "pc", "x", "IX", 1)], 0)}
    )
    monkeypatch.setattr(ejecucion.sqlserver, "abrir_externo", lambda *a, **k: real)
    monkeypatch.setattr(ejecucion.conexiones, "servidor_de", lambda c: "h")
    monkeypatch.setattr(ejecucion.cifrado, "descifrar", lambda p: "x")
    conexion = SimpleNamespace(usuario_sql="u", password_sql_cifrada="p")

    assert (
        ejecucion.escribir(conexion, "Ventas", "UPDATE t SET a = 1 WHERE b = 2")["filas_afectadas"]
        == 4
    )
    assert real.ejecutadas[0] == "SET LOCK_TIMEOUT 5000" and "commit" in real.estado

    tablas = (Tabla("Ventas", "dbo", "t"), Tabla("Ventas", "dbo", "u"))
    [sesion] = ejecucion.bloqueos(conexion, "Ventas", tablas)
    assert sesion["sesion"] == 57 and sesion["tablas"] == ["Ventas.dbo.t", "Ventas.dbo.u"]

    real.respuestas["UPDATE"] = pyodbc.OperationalError("HYT00", "Lock request time out")
    with pytest.raises(ErrorDominio) as error:
        ejecucion.escribir(conexion, "Ventas", "UPDATE t SET a = 1 WHERE b = 2")
    assert error.value.codigo == "SQL_EXE_000" and real.estado[-2:] == ["rollback", "close"]
    real.respuestas["dm_tran_locks"] = pyodbc.ProgrammingError("42000", "sin VIEW SERVER STATE")
    with pytest.raises(ErrorDominio):
        ejecucion.bloqueos(conexion, "Ventas", tablas)

    def sin_red(*a, **k):
        raise pyodbc.OperationalError("08001", "sin red")

    monkeypatch.setattr(ejecucion.sqlserver, "abrir_externo", sin_red)
    with pytest.raises(ErrorDominio) as error:
        ejecucion.escribir(conexion, "Ventas", "x")
    assert error.value.codigo == "CON_DB_000"


def test_contar_filas_a_modificar(monkeypatch):
    real = _ConexionSql({"COUNT_BIG": (None, [(7,)], -1)})
    monkeypatch.setattr(ejecucion.sqlserver, "abrir_externo", lambda *a, **k: real)
    monkeypatch.setattr(ejecucion.conexiones, "servidor_de", lambda c: "h")
    monkeypatch.setattr(ejecucion.cifrado, "descifrar", lambda p: "x")
    conexion = SimpleNamespace(usuario_sql="u", password_sql_cifrada="p")
    consulta = "SELECT COUNT_BIG(*) FROM dbo.t WHERE a = 1"

    assert ejecucion.contar(conexion, "Ventas", consulta) == 7
    assert real.ejecutadas[0] == "SET LOCK_TIMEOUT 5000" and real.timeout == 10
    # Si no se puede contar (bloqueo, timeout, sin red) simplemente no se informa.
    real.respuestas["COUNT_BIG"] = pyodbc.OperationalError("HYT00", "Lock request time out")
    assert ejecucion.contar(conexion, "Ventas", consulta) is None

    def sin_red(*a, **k):
        raise pyodbc.OperationalError("08001", "sin red")

    monkeypatch.setattr(ejecucion.sqlserver, "abrir_externo", sin_red)
    assert ejecucion.contar(conexion, "Ventas", consulta) is None


def test_valor_json():
    assert ejecucion.valor_json(Decimal("1.50")) == "1.50"
    assert ejecucion.valor_json(datetime(2026, 1, 2, 3, 4)) == "2026-01-02T03:04:00"
    assert ejecucion.valor_json(b"\x01\xff") == "0x01FF"
    assert ejecucion.valor_json(b"x" * 100).startswith("eHh4")
    assert ejecucion.valor_json(UUID(int=1)) == "00000000-0000-0000-0000-000000000001"
    assert ejecucion.valor_json(None) is None and ejecucion.valor_json(object()).startswith("<")


# --- API ----------------------------------------------------------------------------


async def test_api_chat(chat):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    navegador = await _navegador(chat, "DBA")
    usuario = next(
        u for u in chat["usuarios"].usuarios.values() if u.email == "dba@acme.com" and u.id != "u"
    )
    chat["usuarios"].usuarios[usuario.id] = replace(usuario, ambientes=("DEV",))

    assert navegador.get("/api/chat/conexiones").json()["conexiones"][0]["id"] == conexion.id
    chat["respuestas"].append(_traduccion("SELECT id, cuit FROM dbo.clientes"))
    r = navegador.post(
        "/api/chat/mensajes",
        json={"texto": "clientes", "conexion_id": conexion.id},
        headers=CABECERA,
    )
    assert r.status_code == 200
    hilo_id, mensaje = r.json()["hilo_id"], r.json()["respuesta"]
    assert navegador.get("/api/chat/hilos").json()["hilos"][0]["id"] == hilo_id
    assert len(navegador.get(f"/api/chat/hilos/{hilo_id}").json()["mensajes"]) == 2

    revision = navegador.post(
        f"/api/chat/hilos/{hilo_id}/revisar",
        json={"sql": "ALTER TABLE dbo.t ADD c INT", "base": "Ventas"},
        headers=CABECERA,
    )
    assert revision.json()["motivo"] == "sandbox"

    base = f"/api/chat/hilos/{hilo_id}/mensajes/{mensaje['id']}"
    ejecutada = navegador.post(
        f"{base}/ejecutar", json={"sql": mensaje["sql"], "base": "Ventas"}, headers=CABECERA
    )
    assert ejecutada.json()["total"] == 5000
    bloqueada = navegador.post(
        f"{base}/ejecutar", json={"sql": "DELETE FROM dbo.t", "base": "Ventas"}, headers=CABECERA
    )
    assert bloqueada.status_code == 422 and bloqueada.json()["codigo"] == "SQL_VAL_002"

    csv = navegador.get(f"{base}/csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.text == "﻿id,cuit\r\n1,****\r\n2,\r\n"
    assert (
        chat["transacciones"][-1][0] == "chat.exportado"
        and chat["transacciones"][-1][1]["filas"] == 2
    )

    assert (
        navegador.post(
            f"{base}/sandbox", json={"sql": "SELECT 1", "base": "Nada"}, headers=CABECERA
        ).status_code
        == 422
    )
    assert navegador.delete(f"/api/chat/hilos/{hilo_id}", headers=CABECERA).status_code == 204
    assert navegador.get(f"/api/chat/hilos/{hilo_id}").status_code == 404


def test_describir_modificaciones():
    sql = "UPDATE u SET estado = 0 FROM dbo.usuario u WHERE u.id = 5"
    assert analizador.describir(sql, "V") == {
        "operacion": "UPDATE",
        "tabla": "dbo.usuario",
        "cambios": [{"columna": "estado", "valor": "0"}],
        "condicion": "u.id = 5",
        "filas": None,
        "desde_consulta": False,
    }
    insercion = analizador.describir("INSERT INTO dbo.t (a, b) VALUES ('x', 1), ('y', 2)", "V")
    assert insercion["filas"] == 2 and insercion["cambios"] == [
        {"columna": "a", "valor": "'x'"},
        {"columna": "b", "valor": "1"},
    ]
    desde = analizador.describir("INSERT INTO dbo.h (a) SELECT a FROM dbo.t WHERE b = 2", "V")
    assert desde["desde_consulta"] is True and desde["condicion"] == "b = 2"
    assert analizador.describir("SELECT 1", "V") is None
    assert analizador.describir("UPDATE a SET b = 1; UPDATE c SET d = 1", "V") is None
    # El conteo previo solo para modificaciones simples.
    assert analizador.consulta_de_conteo("UPDATE dbo.t SET a = 1 WHERE b = 2") == (
        "SELECT COUNT_BIG(*) FROM dbo.t WHERE b = 2"
    )
    for sql in (
        "UPDATE u SET a = 1 FROM dbo.t u JOIN dbo.r r ON r.id = u.id WHERE r.x = 1",
        "DELETE TOP (10) FROM dbo.t WHERE a = 1",
        "UPDATE dbo.t SET a = 1",
        "INSERT INTO dbo.t (a) VALUES (1)",
    ):
        assert analizador.consulta_de_conteo(sql) is None
