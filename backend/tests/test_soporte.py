import logging
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core import excepciones, procesos
from app.core.errores import ErrorDominio
from app.main import app
from app.modulos.soporte import historia, repositorios, servicios
from app.modulos.soporte.entidades import TablaConHistoria
from tests.test_conexiones import (
    CABECERA,
    TENANT,
    _actor,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)

TS = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
TABLA = {
    "tipo": "tabla",
    "nombre": "dbo.usuario",
    "definicion": "CREATE TABLE [dbo].[usuario] (x);",
}
INDICE = {"tipo": "indice", "nombre": "dbo.usuario.IX", "definicion": "CREATE INDEX IX ON u (x);"}
TRIGGER = {"tipo": "trigger", "nombre": "trg_historial_ddl", "definicion": "CREATE TRIGGER t"}


# --- Estructura de la base de la aplicación -----------------------------------------


@pytest.fixture
def estructura(entorno, monkeypatch):  # noqa: F811
    estado = {
        "objetos": {"tabla:dbo.usuario": TABLA},
        "migraciones": ["001", "014"],
        "versiones": [],
        "eventos": [],
    }

    async def ultima_version(base, tenant_id):
        return estado["versiones"][-1] if estado["versiones"] else None

    async def guardar_version(base, documento):
        numero = len(estado["versiones"]) + 1
        estado["versiones"].append({**documento, "version": numero, "ts": TS})
        return numero

    async def listar_versiones(base, tenant_id):
        return list(reversed(estado["versiones"]))

    async def obtener_version(base, tenant_id, version):
        return next((v for v in estado["versiones"] if v["version"] == version), None)

    async def registrar(base, evento):
        estado["eventos"].append((evento.accion, evento.detalle))

    monkeypatch.setattr(repositorios, "capturar", lambda base: dict(estado["objetos"]))
    monkeypatch.setattr(repositorios, "migraciones_aplicadas", lambda base: estado["migraciones"])
    monkeypatch.setattr(repositorios, "ultima_version", ultima_version)
    monkeypatch.setattr(repositorios, "guardar_version", guardar_version)
    monkeypatch.setattr(repositorios, "listar_versiones", listar_versiones)
    monkeypatch.setattr(repositorios, "obtener_version", obtener_version)
    monkeypatch.setattr(historia.auditoria, "registrar", registrar)
    return {**entorno, "estado": estado}


async def test_versiones_por_migracion_cambio_externo_y_captura_manual(estructura):
    estado = estructura["estado"]
    # Primera captura: estructura inicial, sin diff.
    assert await historia.registrar_estructura(TENANT) == 1
    assert estado["versiones"][0]["origen"] == "inicial"
    assert estado["versiones"][0]["cambios"] is None
    # Sin cambios no hay versión nueva.
    assert await historia.registrar_estructura(TENANT) is None

    # Migración nueva: la versión dice cuáles se aplicaron.
    estado["objetos"]["indice:dbo.usuario.IX"] = INDICE
    estado["migraciones"] = ["001", "014", "015"]
    assert await historia.registrar_estructura(TENANT) == 2
    segunda = estado["versiones"][1]
    assert segunda["origen"] == "migracion" and segunda["migraciones"] == ["015"]
    assert [c["operacion"] for c in segunda["cambios"]] == ["agregado"]

    # Cambio sin migraciones nuevas: hecho por fuera de la aplicación.
    estado["objetos"]["trigger:trg_historial_ddl"] = TRIGGER
    await historia.registrar_estructura_todo([TENANT])
    assert estado["versiones"][2]["origen"] == "detectado"

    # Captura a pedido: sin cambios no guarda, pero queda auditada.
    assert await historia.capturar_ahora(TENANT, _actor("ADMINISTRADOR")) == {"version": None}
    del estado["objetos"]["indice:dbo.usuario.IX"]
    assert await historia.capturar_ahora(TENANT, _actor("ADMINISTRADOR")) == {"version": 4}
    assert estado["versiones"][3]["origen"] == "manual"
    assert estado["versiones"][3]["usuario"] == "dba@acme.com"
    assert estado["eventos"][-1] == (
        "soporte.estructura_capturada",
        {"base": "DB_T_acme", "version": 4},
    )

    listado = await historia.versiones(TENANT)
    assert listado["base"] == "DB_T_acme"
    assert [v["version"] for v in listado["versiones"]] == [4, 3, 2, 1]
    assert listado["versiones"][-1]["inicial"] is True
    assert listado["versiones"][2]["resumen"] == {"agregado": 1, "eliminado": 0, "modificado": 0}

    detalle = await historia.detalle(TENANT, 4)
    assert detalle["detalle"][0]["operacion"] == "eliminado"
    with pytest.raises(ErrorDominio) as error:
        await historia.detalle(TENANT, 99)
    assert error.value.codigo == "SCH_SNP_000"


async def test_comparar_y_script_de_una_version(estructura):
    estado = estructura["estado"]
    await historia.registrar_estructura(TENANT)
    estado["objetos"]["indice:dbo.usuario.IX"] = INDICE
    await historia.registrar_estructura(TENANT)
    estado["objetos"]["trigger:trg_historial_ddl"] = TRIGGER

    entre = await historia.comparar(TENANT, 1, "2")
    assert entre["resumen"] == {"agregado": 1, "eliminado": 0, "modificado": 0}
    actual = await historia.comparar(TENANT, 1, historia.ACTUAL)
    assert actual["resumen"]["agregado"] == 2
    for hasta in ("1", "x"):
        with pytest.raises(ErrorDominio):
            await historia.comparar(TENANT, 1, hasta)

    script = await historia.script(TENANT, 2)
    assert script.startswith("-- Estructura de DB_T_acme, versión 2")
    # Primero las tablas, después los índices; cada objeto en su lote.
    assert script.index("CREATE TABLE") < script.index("CREATE INDEX")
    assert script.count("\nGO\n") == 2


async def test_registrar_estructura_todo_no_corta_el_arranque(estructura, monkeypatch, caplog):
    def falla(base):
        raise ConnectionError("sin SQL Server")

    monkeypatch.setattr(repositorios, "capturar", falla)
    with caplog.at_level(logging.ERROR):
        await historia.registrar_estructura_todo([TENANT])
    assert "No se pudo registrar la estructura del tenant acme" in caplog.text


# --- Historial DDL y datos (tablas temporales) --------------------------------------


async def test_historial_ddl(entorno, monkeypatch):  # noqa: F811
    pedidos = []

    def historial(base, texto, salto, cantidad):
        pedidos.append((base, texto, salto, cantidad))
        return [{"id": 7, "ts": datetime(2026, 11, 20, 15), "evento": "ALTER_TABLE"}], 51

    monkeypatch.setattr(repositorios, "historial_ddl", historial)
    monkeypatch.setattr(
        repositorios,
        "ddl",
        lambda base, i: {"id": i, "ts": TS, "tsql": "ALTER TABLE x"} if i == 7 else None,
    )
    r = await historia.historial_ddl(TENANT, 2, "  usuario ")
    assert pedidos == [("DB_T_acme", "usuario", 50, 50)]
    assert (r["total"], r["pagina"], r["paginas"]) == (51, 2, 2)
    assert r["registros"][0]["ts"] == "2026-11-20T15:00:00+00:00"
    await historia.historial_ddl(TENANT, 0, "")
    assert pedidos[-1] == ("DB_T_acme", None, 0, 50)
    assert (await historia.ddl(TENANT, 7))["tsql"] == "ALTER TABLE x"
    with pytest.raises(ErrorDominio):
        await historia.ddl(TENANT, 8)


async def test_datos_al_momento_y_cambios(entorno, monkeypatch):  # noqa: F811
    definicion = TablaConHistoria("conexion", ("id", "alias", "activa"), ("id",))
    pedidos = []
    fila = (
        datetime(2026, 11, 1, 3),
        datetime(9999, 12, 31, 23, 59, 59),
        uuid.UUID(int=1),
        "ERP",
        True,
    )

    def al_momento(base, tabla, tenant_id, momento, clave):
        pedidos.append(("momento", momento, clave))
        return ["valido_desde", "valido_hasta", *tabla.columnas], [fila]

    def cambios(base, tabla, tenant_id, clave):
        pedidos.append(("cambios", None, clave))
        return ["valido_desde", "valido_hasta", *tabla.columnas], [fila] * 500

    monkeypatch.setattr(repositorios, "tabla_con_historia", lambda base, tabla: definicion)
    monkeypatch.setattr(repositorios, "filas_al_momento", al_momento)
    monkeypatch.setattr(repositorios, "cambios", cambios)

    # El momento llega con zona (el navegador manda UTC); se consulta en UTC sin zona.
    momento = datetime(2026, 11, 20, 12, tzinfo=UTC) - timedelta(hours=3)
    r = await historia.datos(TENANT, "conexion", "momento", momento, " ")
    assert pedidos[-1] == ("momento", datetime(2026, 11, 20, 9), None)
    assert r["columnas"][:2] == ["valido_desde", "valido_hasta"]
    assert r["filas"][0][2:] == ["00000000-0000-0000-0000-000000000001", "ERP", True]
    assert r["filas"][0][0] == "2026-11-01T03:00:00+00:00" and r["truncado"] is False

    r = await historia.datos(TENANT, "conexion", "cambios", None, "abc")
    assert pedidos[-1] == ("cambios", None, "abc") and r["truncado"] is True

    for tabla, modo, momento in (
        ("tenant", "momento", TS),
        ("conexion", "otro", TS),
        ("conexion", "momento", None),
    ):
        with pytest.raises(ErrorDominio):
            await historia.datos(TENANT, tabla, modo, momento, None)
    monkeypatch.setattr(repositorios, "tabla_con_historia", lambda base, tabla: None)
    with pytest.raises(ErrorDominio):
        await historia.datos(TENANT, "conexion", "cambios", None, None)


def test_valores_para_la_pantalla():
    assert historia._valor(Decimal("1.5")) == 1.5
    assert historia._valor(date(2026, 1, 2)) == "2026-01-02"
    assert historia._valor(b"\x01\xff") == "01ff"
    assert historia._valor(None) is None


class _Cursor:
    def __init__(self, respuestas, ejecutadas):
        self.respuestas, self.ejecutadas = respuestas, ejecutadas
        self.description = None
        self.filas = []

    def execute(self, sql, *parametros):
        self.ejecutadas.append((" ".join(sql.split()), parametros))
        respuesta = next(r for c, r in self.respuestas.items() if c in sql)
        # Una respuesta es la lista de filas o (filas, nombres de las columnas).
        self.filas, columnas = respuesta if isinstance(respuesta, tuple) else (respuesta, ())
        self.description = [(c,) for c in columnas]
        return self

    def fetchall(self):
        return self.filas

    def fetchone(self):
        return self.filas[0] if self.filas else None


@pytest.fixture
def sql_falso(monkeypatch):
    ejecutadas, respuestas = [], {}

    @contextmanager
    def conectar(base):
        class _Conexion:
            def cursor(self):
                return _Cursor(respuestas, ejecutadas)

        yield _Conexion()

    monkeypatch.setattr(repositorios.sqlserver, "conectar", conectar)
    return respuestas, ejecutadas


def test_consultas_de_la_historia_de_datos(sql_falso):
    respuestas, ejecutadas = sql_falso
    respuestas["generated_always_type"] = [
        ("tenant_id", False, None),
        ("metrica", True, 2),
        ("conexion_id", True, 1),
        ("password_sql_cifrada", False, None),
    ]
    tabla = repositorios.tabla_con_historia("DB_T_acme", "umbral_metrica")
    # Las columnas sensibles no se muestran; la clave sigue el orden de la clave primaria.
    assert tabla.columnas == ("tenant_id", "metrica", "conexion_id")
    assert tabla.clave == ("conexion_id", "metrica")

    respuestas["FOR SYSTEM_TIME"] = [("a", "b")]
    momento = datetime(2026, 11, 20, 9)
    columnas, _ = repositorios.filas_al_momento("DB_T_acme", tabla, "t1", momento, "c1")
    assert columnas[:2] == ["valido_desde", "valido_hasta"]
    sql, parametros = ejecutadas[-1]
    assert "FROM dbo.[umbral_metrica] FOR SYSTEM_TIME AS OF ?" in sql
    assert "CAST([conexion_id] AS NVARCHAR(400)) = ?" in sql
    assert "ORDER BY [conexion_id], [metrica]" in sql
    assert parametros == (momento, "t1", "c1")
    repositorios.cambios("DB_T_acme", tabla, "t1", None)
    sql, parametros = ejecutadas[-1]
    assert "FOR SYSTEM_TIME ALL WHERE tenant_id = ? ORDER BY [valido_desde] DESC" in sql
    assert parametros == ("t1",)

    respuestas["generated_always_type"] = []
    assert repositorios.tabla_con_historia("DB_T_acme", "otra") is None


def test_consultas_del_historial_ddl(sql_falso):
    respuestas, ejecutadas = sql_falso
    respuestas["COUNT(*)"] = [(3,)]
    respuestas["ORDER BY id DESC"] = ([(1, TS, "CREATE_TABLE")], ("id", "ts", "evento"))
    respuestas["WHERE id = ?"] = ([(1, "CREATE TABLE x")], ("id", "tsql"))
    filas, total = repositorios.historial_ddl("DB_T_acme", "usu", 0, 50)
    assert total == 3 and filas == [{"id": 1, "ts": TS, "evento": "CREATE_TABLE"}]
    assert ejecutadas[0][1] == ("%usu%",) * 4
    assert ejecutadas[1][1] == ("%usu%",) * 4 + (0, 50)
    repositorios.historial_ddl("DB_T_acme", None, 50, 50)
    assert "WHERE" not in ejecutadas[-1][0] and ejecutadas[-1][1] == (50, 50)
    assert repositorios.ddl("DB_T_acme", 1) == {"id": 1, "tsql": "CREATE TABLE x"}
    respuestas["WHERE id = ?"] = []
    assert repositorios.ddl("DB_T_acme", 2) is None
    respuestas["migracion_aplicada"] = [("001",), ("014",)]
    assert repositorios.migraciones_aplicadas("DB_T_acme") == ["001", "014"]


# --- Servicios, colas y procesos ----------------------------------------------------


async def test_salud_de_los_servicios(entorno, monkeypatch):  # noqa: F811
    async def ok():
        return None

    async def caido():
        raise ConnectionError("sin respuesta")

    monkeypatch.setattr(servicios, "_sqlserver", ok)
    monkeypatch.setattr(servicios.mongo, "verificar", caido)
    monkeypatch.setattr(servicios.cache, "verificar", ok)
    monkeypatch.setattr(servicios.ClienteAuth0, "verificar", lambda self: ok())
    monkeypatch.setattr(servicios.sandbox, "salud", ok)
    monkeypatch.setenv("SECRETO_SANDBOX_TOKEN", "x")
    monkeypatch.setenv("AUTH0_DOMAIN", "dba-test.us.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "cliente-web")
    servicios.obtener_configuracion.cache_clear()

    estados = {s["nombre"]: (s["estado"], s["detalle"]) for s in await servicios.servicios(TENANT)}
    assert estados == {
        "SQL Server": ("online", "DB_T_acme"),
        "MongoDB": ("error", "db_t_acme"),
        "Redis": ("online", "Sesiones y caché"),
        "Auth0": ("online", "dba-test.us.auth0.com"),
        "Sandbox": ("online", "Contenedores de validación"),
    }
    monkeypatch.delenv("SECRETO_SANDBOX_TOKEN")
    monkeypatch.delenv("AUTH0_CLIENT_ID")
    servicios.obtener_configuracion.cache_clear()
    sin_configurar = {s["nombre"]: s for s in await servicios.servicios(TENANT)}
    for nombre in ("Auth0", "Sandbox"):
        assert sin_configurar[nombre]["estado"] == "warning"
        assert sin_configurar[nombre]["detalle"] == "No configurado"
    servicios.obtener_configuracion.cache_clear()


def test_colas_y_procesos(monkeypatch):
    procesos.limpiar()
    monkeypatch.setenv("MONITOR_RECOLECTOR_ACTIVO", "true")
    monkeypatch.setenv("SANDBOX_PROGRAMADOR_ACTIVO", "true")
    servicios.obtener_configuracion.cache_clear()
    ahora = datetime.now(UTC)
    try:
        assert {p["estado"] for p in servicios.colas_y_procesos(ahora)["procesos"]} == {"sin_datos"}
        procesos.latido("recolector", 3, True, conexiones=2, en_curso=0)
        procesos.latido("programador_sandbox", 60, False)
        estado = {p["nombre"]: p for p in servicios.colas_y_procesos(ahora)["procesos"]}
        assert estado["recolector"]["estado"] == "activo"
        assert estado["recolector"]["conexiones"] == 2
        assert estado["programador_sandbox"]["estado"] == "en_espera"
        # Sin latidos en tres intervalos, el proceso figura detenido.
        despues = ahora + timedelta(seconds=10)
        assert servicios.colas_y_procesos(despues)["procesos"][0]["estado"] == "detenido"

        monkeypatch.setenv("SANDBOX_PROGRAMADOR_ACTIVO", "false")
        servicios.obtener_configuracion.cache_clear()
        assert servicios.colas_y_procesos(ahora)["procesos"][1]["estado"] == "desactivado"
    finally:
        procesos.limpiar()
        servicios.obtener_configuracion.cache_clear()

    # Cola de la bitácora: sin iniciar no hay datos; iniciada, informa sus contadores.
    excepciones.detener()
    assert servicios.colas_y_procesos(ahora)["colas"] == []
    manejador = excepciones.ManejadorExcepciones()
    manejador.descartados, manejador.ultima_falla = 2, ahora
    monkeypatch.setattr(excepciones, "_manejador", manejador)
    cola = servicios.colas_y_procesos(ahora)["colas"][0]
    assert cola["nombre"] == "bitacora_logs" and cola["descartados"] == 2
    assert cola["capacidad"] == excepciones.CAPACIDAD_COLA and cola["escritor_activo"] is False
    assert cola["ultima_falla"] == ahora.isoformat()


# --- API y registro de errores HTTP -------------------------------------------------


async def test_api_de_soporte_solo_para_el_administrador(estructura, monkeypatch):
    async def servicios_ok(tenant):
        return [{"nombre": "Redis", "estado": "online", "latencia_ms": 1, "detalle": "x"}]

    async def resumen(tenant, sesion):
        return {"bitacoras": ["sistema", "excepciones"]}

    monkeypatch.setattr(servicios, "servicios", servicios_ok)
    monkeypatch.setattr(servicios, "resumen_bitacoras", resumen)
    monkeypatch.setattr(repositorios, "historial_ddl", lambda *a: ([], 0))
    monkeypatch.setattr(
        repositorios,
        "tabla_con_historia",
        lambda base, tabla: TablaConHistoria(tabla, ("id",), ("id",)),
    )
    monkeypatch.setattr(repositorios, "cambios", lambda *a: (["id"], [(1,)]))

    admin = await _navegador(estructura, "ADMINISTRADOR")
    assert admin.get("/api/soporte/servicios").json()["servicios"][0]["nombre"] == "Redis"
    assert set(admin.get("/api/soporte/procesos").json()) == {"colas", "procesos"}
    assert admin.get("/api/soporte/bitacoras").json()["bitacoras"] == ["sistema", "excepciones"]
    assert admin.post("/api/soporte/estructura", headers=CABECERA).json() == {"version": 1}
    assert admin.get("/api/soporte/estructura").json()["versiones"][0]["version"] == 1
    assert admin.get("/api/soporte/estructura/1").json()["inicial"] is True
    assert admin.get("/api/soporte/estructura/comparar?desde=1").json()["cambios"] == []
    script = admin.get("/api/soporte/estructura/1/script")
    assert "estructura_DB_T_acme_v1.sql" in script.headers["content-disposition"]
    assert admin.get("/api/soporte/ddl?pagina=1").json()["total"] == 0
    assert admin.get("/api/soporte/datos").json()["tablas"][0] == "usuario"
    assert admin.get("/api/soporte/datos/conexion?modo=cambios").json()["filas"] == [[1]]
    assert "Detrás de" in admin.get("/soporte").text
    # La salud de los servicios ya no está en el Panel Admin.
    assert "estadoServicios" not in admin.get("/admin").text

    dba = await _navegador(estructura, "DBA")
    assert dba.get("/api/soporte/servicios").status_code == 403
    assert dba.get("/api/soporte/bitacoras/excepciones").status_code == 403
    assert dba.get("/soporte").status_code in (302, 303, 403)


async def test_respuestas_con_error_quedan_en_la_bitacora(estructura, monkeypatch, caplog):
    admin = await _navegador(estructura, "ADMINISTRADOR")
    dba = await _navegador(estructura, "DBA")
    with caplog.at_level(logging.WARNING, logger="app.http"):
        assert admin.get("/api/no-existe").status_code == 404
        # Error de negocio: queda con su código y el usuario de la sesión.
        assert dba.get("/api/soporte/servicios").status_code == 403

        async def falla(tenant):
            raise RuntimeError("se rompió")

        monkeypatch.setattr(servicios, "servicios", falla)
        with pytest.raises(RuntimeError):
            admin.get("/api/soporte/servicios")

    registros = [r for r in caplog.records if r.name == "app.http"]
    no_existe, prohibido, roto = registros
    assert no_existe.levelno == logging.WARNING and no_existe.getMessage() == (
        "GET /api/no-existe 404"
    )
    assert no_existe.http["estado"] == 404 and no_existe.http["usuario"] is None
    # Sin ruta que la atienda no hay plantilla; con ruta, la plantilla sin valores.
    assert no_existe.http["plantilla"] is None
    assert roto.http["plantilla"] == "/api/soporte/servicios"
    assert prohibido.getMessage().startswith("GET /api/soporte/servicios 403 USR_PER_000")
    assert prohibido.http["usuario"] == "dba@acme.com"
    assert roto.levelno == logging.ERROR and roto.http["estado"] == 500
    assert roto.exc_info and "se rompió" in str(roto.exc_info[1])
    documento = excepciones.documento(roto, "acme", None)
    assert documento["origen"] == "http" and "RuntimeError" in documento["traza"]


def test_sin_errores_no_se_registra_nada(caplog):
    navegador = TestClient(app, base_url="https://localhost")
    with caplog.at_level(logging.WARNING, logger="app.http"):
        navegador.get("/manifest.webmanifest")
    assert not [r for r in caplog.records if r.name == "app.http"]


async def test_api_de_bitacoras_de_soporte(estructura, monkeypatch):
    from app.modulos.soporte import api

    llamadas = []

    async def listar(tenant, sesion, nombre, filtros, pagina):
        llamadas.append(("listar", nombre, filtros.origen, pagina))
        return {"registros": [], "total": 0}

    async def detalle(tenant, sesion, nombre, registro_id):
        llamadas.append(("detalle", nombre, registro_id))
        return {"id": registro_id}

    async def exportar_csv(tenant, sesion, nombre, filtros):
        async def lineas():
            yield "Fecha\r\n"

        return lineas()

    async def nivel(tenant, sesion, valor):
        return valor.upper()

    monkeypatch.setattr(api.auditoria, "listar", listar)
    monkeypatch.setattr(api.auditoria, "detalle", detalle)
    monkeypatch.setattr(api.auditoria, "exportar_csv", exportar_csv)
    monkeypatch.setattr(api.auditoria, "cambiar_nivel_excepciones", nivel)

    admin = await _navegador(estructura, "ADMINISTRADOR")
    assert admin.get("/api/soporte/bitacoras/excepciones?origen=http&pagina=2").status_code == 200
    assert admin.get("/api/soporte/bitacoras/sistema/registros/abc").json() == {"id": "abc"}
    csv = admin.get("/api/soporte/bitacoras/excepciones/exportar?formato=csv")
    assert csv.status_code == 200 and "auditoria_excepciones_" in csv.headers["content-disposition"]
    assert admin.put(
        "/api/soporte/bitacoras/excepciones/nivel", json={"nivel": "info"}, headers=CABECERA
    ).json() == {"nivel": "INFO"}
    assert llamadas == [("listar", "excepciones", "http", 2), ("detalle", "sistema", "abc")]
    # Las transacciones se consultan desde Auditoría.
    assert admin.get("/api/soporte/bitacoras/transacciones").status_code == 422
