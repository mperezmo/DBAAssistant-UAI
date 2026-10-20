from datetime import UTC, datetime
from types import SimpleNamespace

import pyodbc
import pytest

from app.core.errores import ErrorDominio
from app.modulos.chat_ia.analizador import Tabla, analizar
from app.modulos.conexiones import servicios as conexiones
from app.modulos.contexto_negocio import esquema, servicios
from app.modulos.contexto_negocio.entidades import Regla, TablaSensible, Termino
from tests import contexto_falso
from tests.test_conexiones import (
    CABECERA,
    TENANT,
    _actor,
    _datos,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)

OBTENER_ESQUEMA = esquema.obtener
LEER_ESQUEMA = esquema.leer
ESQUEMA = {
    "Ventas": [
        {
            "esquema": "dbo",
            "nombre": "clientes",
            "vista": False,
            "columnas": [
                {"nombre": "id", "tipo": "int", "nula": False},
                {"nombre": "cuit", "tipo": "varchar(11)", "nula": True},
                {"nombre": "estado", "tipo": "int", "nula": False},
            ],
            "pk": ["id"],
            "fk": [],
        },
        {
            "esquema": "dbo",
            "nombre": "pedidos",
            "vista": False,
            "columnas": [{"nombre": "cliente_id", "tipo": "int", "nula": False}],
            "pk": [],
            "fk": [{"columna": "cliente_id", "referencia": "dbo.clientes.id"}],
        },
    ]
}


@pytest.fixture
def ctx(entorno, monkeypatch):  # noqa: F811
    almacen = contexto_falso.instalar(monkeypatch)

    async def obtener_esquema(tenant, conexion_id, refrescar=False):
        return ESQUEMA

    monkeypatch.setattr(esquema, "obtener", obtener_esquema)
    return {**entorno, "almacen": almacen}


# --- Analizador ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "tipo", "falta_where", "destructiva"),
    [
        ("SELECT c.cuit AS x FROM dbo.clientes c WHERE c.id = 1", "SELECT", False, False),
        ("WITH a AS (SELECT * FROM dbo.t) SELECT * FROM a", "SELECT", False, False),
        ("UPDATE dbo.t SET a = 1 WHERE id = 2", "UPDATE", False, False),
        ("UPDATE dbo.t SET a = 1", "UPDATE", True, False),
        ("DELETE FROM dbo.t", "DELETE", True, True),
        ("INSERT INTO dbo.t (a) VALUES (1)", "INSERT", False, False),
        ("INSERT INTO dbo.t (a) SELECT a FROM dbo.g", "INSERT", True, False),
        ("INSERT INTO dbo.t (a) SELECT a FROM dbo.g WHERE a > 1", "INSERT", False, False),
        ("INSERT INTO dbo.t EXEC dbo.sp", "SANDBOX", False, False),
        ("SELECT * INTO dbo.nueva FROM dbo.g", "SANDBOX", False, False),
        ("ALTER TABLE dbo.t ADD b INT", "SANDBOX", False, False),
        ("TRUNCATE TABLE dbo.t", "SANDBOX", False, True),
        ("DROP TABLE dbo.t", "SANDBOX", False, True),
        ("EXEC sp_who2", "SANDBOX", False, False),
        ("SELECT 1; SELECT 2", "SANDBOX", False, False),
        ("esto no es sql @@@", "SANDBOX", False, False),
        ("-- DROP en un comentario\nSELECT 1", "SELECT", False, False),
    ],
)
def test_analizador_clasifica(sql, tipo, falta_where, destructiva):
    analisis = analizar(sql, "Ventas")
    assert (analisis.tipo, analisis.falta_where, analisis.destructiva) == (
        tipo,
        falta_where,
        destructiva,
    )


def test_analizador_tablas_sin_alias_ni_cte():
    analisis = analizar(
        "WITH x AS (SELECT 1 a) UPDATE c SET estado = 1 FROM dbo.clientes c "
        "JOIN Otra.ventas.pedidos p ON p.id = c.id JOIN x ON 1 = 1 WHERE c.id = 1",
        "Ventas",
    )
    assert analisis.tablas == (
        Tabla("Ventas", "dbo", "clientes"),
        Tabla("Otra", "ventas", "pedidos"),
    )
    assert analisis.acciones == {"UPDATE"} and analisis.es_escritura
    assert analizar("MERGE dbo.t USING dbo.s ON 1 = 1 WHEN MATCHED THEN DELETE;", "V").acciones == {
        "UPDATE"
    }


# --- Contexto: alta, modificación y consulta ----------------------------------------


async def test_tablas_sensibles(ctx):
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    datos = TablaSensible(
        conexion.id, "Ventas", "DBO.Clientes", ("CUIT",), ("ADMINISTRADOR", "DBA"), ("DBA",), True
    )
    creada = await servicios.guardar_sensible(TENANT, _actor(), None, datos)
    assert creada["tabla"] == "dbo.clientes" and creada["columnas"] == ["cuit"]
    assert ctx["eventos"][-1][0] == "contexto.sensible_creado"

    errores = [
        (_actor("CONSULTA"), datos, "USR_PER_000"),
        (
            _actor(),
            TablaSensible(conexion.id, "Ventas", "", (), ("DBA",), (), False),
            "USR_VAL_002",
        ),
        (
            _actor(),
            TablaSensible(conexion.id, "Ventas", "dbo.x", (), ("DBA",), (), True),
            "CTX_VAL_003",
        ),
        (
            _actor(),
            TablaSensible(conexion.id, "Ventas", "dbo.pedidos", ("nada",), ("DBA",), (), True),
            "CTX_VAL_003",
        ),
        # Ver las columnas sin consultar la tabla es incoherente; sin restricciones también.
        (
            _actor(),
            TablaSensible(conexion.id, "Ventas", "dbo.pedidos", (), ("DBA",), ("CONSULTA",), True),
            "CTX_VAL_002",
        ),
        (
            _actor(),
            TablaSensible(
                conexion.id,
                "Ventas",
                "dbo.pedidos",
                (),
                ("ADMINISTRADOR", "DBA", "CONSULTA"),
                (),
                False,
            ),
            "CTX_VAL_002",
        ),
        (_actor(), datos, "CTX_VAL_002"),  # ya marcada
    ]
    for actor, entrada, codigo in errores:
        with pytest.raises(ErrorDominio) as error:
            await servicios.guardar_sensible(TENANT, actor, None, entrada)
        assert error.value.codigo == codigo

    modificada = await servicios.guardar_sensible(
        TENANT,
        _actor(),
        creada["id"],
        TablaSensible(**{**datos.__dict__, "doble_confirmacion": False}),
    )
    assert modificada["doble_confirmacion"] is False
    accion, detalle = ctx["eventos"][-1]
    assert accion == "contexto.sensible_modificado"
    assert detalle["anterior"]["doble_confirmacion"] is True
    assert detalle["nuevo"]["doble_confirmacion"] is False

    listado = await servicios.listar(TENANT, conexion.id, "Ventas")
    assert [s["tabla"] for s in listado["sensibles"]] == ["dbo.clientes"]
    await servicios.eliminar(TENANT, _actor(), "sensible", creada["id"])
    assert (await servicios.listar(TENANT, conexion.id, None))["sensibles"] == []
    for tipo, elemento in (("sensible", creada["id"]), ("otro", "x")):
        with pytest.raises(ErrorDominio) as error:
            await servicios.eliminar(TENANT, _actor(), tipo, elemento)
        assert error.value.codigo == "CTX_VAL_000"


async def test_glosario(ctx):
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    termino = Termino(conexion.id, "Ventas", "Cliente activo", "clientes", ("cliente vigente", " "))
    creado = await servicios.guardar_termino(TENANT, _actor(), None, termino)
    assert creado["entidad"] == "dbo.clientes" and creado["sinonimos"] == ["cliente vigente"]
    for entrada, codigo in (
        (Termino(conexion.id, "Ventas", "cliente ACTIVO", "dbo.clientes"), "CTX_VAL_004"),
        (Termino(conexion.id, "Ventas", "x", "dbo.inexistente"), "CTX_VAL_003"),
        (Termino(conexion.id, "Ventas", "", "dbo.clientes"), "USR_VAL_002"),
    ):
        with pytest.raises(ErrorDominio) as error:
            await servicios.guardar_termino(TENANT, _actor(), None, entrada)
        assert error.value.codigo == codigo
    with pytest.raises(ErrorDominio) as error:
        await servicios.guardar_termino(TENANT, _actor(), "no-existe", termino)
    assert error.value.codigo == "CTX_VAL_000"


async def test_reglas_y_su_evaluacion(ctx):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="PROD"))
    fuera_de_horario = Regla(
        "No modificar PROD fuera de horario",
        ("UPDATE", "DELETE"),
        "bloquear",
        1,
        ambientes=("PROD",),
        desde=datetime(2026, 1, 1, 18).time(),
        hasta=datetime(2026, 1, 1, 9).time(),
    )
    await servicios.guardar_regla(TENANT, _actor(), None, fuera_de_horario)
    await servicios.guardar_regla(
        TENANT,
        _actor(),
        None,
        Regla(
            "Cuidado con clientes",
            ("SELECT",),
            "advertir",
            3,
            conexion.id,
            tablas=("dbo.clientes",),
        ),
    )
    await servicios.guardar_regla(
        TENANT, _actor(), None, Regla("Inactiva", ("SELECT",), "bloquear", activa=False)
    )
    await servicios.guardar_regla(
        TENANT, _actor(), None, Regla("Otra base", ("SELECT",), "bloquear", base="Compras")
    )
    for regla in (
        Regla("", ("SELECT",), "bloquear"),
        Regla("x", (), "bloquear"),
        Regla("x", ("SELECT",), "ignorar"),
        Regla("x", ("SELECT",), "bloquear", desde=datetime(2026, 1, 1, 9).time()),
    ):
        with pytest.raises(ErrorDominio) as error:
            await servicios.guardar_regla(TENANT, _actor(), None, regla)
        assert error.value.codigo == "USR_VAL_002"

    # 22:00 en Buenos Aires = 01:00 UTC del día siguiente: rige la franja 18 a 9.
    noche = datetime(2026, 5, 4, 1, 0, tzinfo=UTC)
    mediodia = datetime(2026, 5, 4, 15, 0, tzinfo=UTC)
    update = analizar("UPDATE dbo.clientes SET estado = 1 WHERE id = 1", "Ventas")
    bloqueos, advertencias = await servicios.evaluar_reglas(
        TENANT, conexion, "Ventas", update, noche
    )
    assert [b["descripcion"] for b in bloqueos] == ["No modificar PROD fuera de horario"]
    assert advertencias == []
    assert await servicios.evaluar_reglas(TENANT, conexion, "Ventas", update, mediodia) == ([], [])

    lectura = analizar("SELECT * FROM dbo.clientes", "Ventas")
    bloqueos, advertencias = await servicios.evaluar_reglas(TENANT, conexion, "Ventas", lectura)
    assert bloqueos == [] and [a["descripcion"] for a in advertencias] == ["Cuidado con clientes"]
    otra_tabla = analizar("SELECT * FROM dbo.pedidos", "Ventas")
    assert await servicios.evaluar_reglas(TENANT, conexion, "Ventas", otra_tabla) == ([], [])
    compras = analizar("SELECT * FROM dbo.x", "Compras")
    assert [
        b["descripcion"]
        for b in (await servicios.evaluar_reglas(TENANT, conexion, "Compras", compras))[0]
    ] == ["Otra base"]
    dev = await conexiones.crear(TENANT, _actor(), _datos(alias="DEV-01", ambiente="DEV"))
    assert await servicios.evaluar_reglas(TENANT, dev, "Ventas", update, noche) == ([], [])

    listado = await servicios.listar(TENANT, conexion.id, "Ventas")
    assert {r["descripcion"] for r in listado["reglas"]} == {
        "No modificar PROD fuera de horario",
        "Cuidado con clientes",
        "Inactiva",
    }


async def test_aplicacion_y_texto_para_ia(ctx):
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    await servicios.guardar_sensible(
        TENANT,
        _actor(),
        None,
        TablaSensible(
            conexion.id,
            "Ventas",
            "dbo.clientes",
            ("cuit",),
            ("DBA", "ADMINISTRADOR"),
            ("DBA",),
            True,
        ),
    )
    await servicios.guardar_termino(
        TENANT,
        _actor(),
        None,
        Termino(
            conexion.id, "Ventas", "cliente activo", "dbo.clientes", ("vigente",), "estado = 1"
        ),
    )
    await servicios.guardar_regla(
        TENANT, _actor(), None, Regla("Sin DDL en PROD", ("DDL",), "bloquear")
    )

    tablas = analizar("SELECT * FROM ventas.DBO.CLIENTES", "Otra").tablas
    sensibles = await servicios.sensibles_de(TENANT, conexion.id, tablas)
    assert len(sensibles) == 1 and await servicios.sensibles_de(TENANT, conexion.id, ()) == []
    servicios.verificar_acceso(_actor("DBA"), sensibles)
    with pytest.raises(ErrorDominio) as error:
        servicios.verificar_acceso(_actor("CONSULTA"), sensibles)
    assert (
        error.value.codigo == "USR_PER_000" and error.value.datos["tabla"] == "Ventas.dbo.clientes"
    )
    assert servicios.columnas_enmascaradas(_actor("DBA"), sensibles) == set()
    assert servicios.columnas_enmascaradas(_actor("ADMINISTRADOR"), sensibles) == {
        ("ventas", "dbo.clientes", "cuit")
    }

    texto = await servicios.texto_para_ia(TENANT, conexion)
    assert "## Base Ventas" in texto
    assert "- dbo.clientes: id int PK, cuit varchar(11), estado int" in texto
    assert "FK: cliente_id→dbo.clientes.id" in texto
    assert "«cliente activo» (sinónimos: vigente) → Ventas.dbo.clientes: estado = 1" in texto
    assert "[bloquear] Sin DDL en PROD" in texto
    assert "Ventas.dbo.clientes columnas: cuit" in texto


# --- Esquema ------------------------------------------------------------------------


class _Cursor:
    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.filas = []

    def execute(self, sql, *params):
        for clave, filas in self.respuestas.items():
            if clave in sql:
                self.filas = filas
                return self
        raise pyodbc.ProgrammingError("42000", "sin permiso")

    def fetchall(self):
        return self.filas


class _Conexion:
    def __init__(self, respuestas):
        self.respuestas = respuestas

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return _Cursor(self.respuestas)


async def test_lectura_del_esquema_con_cache(ctx, monkeypatch):
    conexion = SimpleNamespace(id="c1", alias="ERP", host="h", puerto=None, instancia=None)
    respuestas = {
        "master": {"FROM sys.databases": [("Ventas",), ("Bloqueada",)]},
        "Ventas": {
            "JOIN sys.types": [
                ("dbo", "clientes", "U ", "id", "int", 4, 0),
                ("dbo", "clientes", "U ", "nombre", "nvarchar", 200, 1),
                ("dbo", "v_activos", "V ", "codigo", "varchar", -1, 1),
            ],
            "is_primary_key": [("dbo", "clientes", "id")],
            "foreign_key_columns": [("dbo", "clientes", "id", "dbo", "otra", "id")],
        },
        "Bloqueada": {},
    }
    monkeypatch.setattr(esquema, "_abrir", lambda c, base="master": _Conexion(respuestas[base]))
    leido = esquema.leer(conexion)
    assert list(leido) == ["Ventas"]
    clientes, vista = leido["Ventas"]
    assert clientes["columnas"][1] == {"nombre": "nombre", "tipo": "nvarchar(100)", "nula": True}
    assert clientes["pk"] == ["id"] and clientes["fk"][0]["referencia"] == "dbo.otra.id"
    assert vista["vista"] is True and vista["columnas"][0]["tipo"] == "varchar(max)"
    assert esquema.buscar_tabla(leido, "ventas", "CLIENTES") is clientes
    assert esquema.buscar_tabla(leido, "ventas", "dbo.nada") is None

    lecturas = []

    def leer(c):
        lecturas.append(c)
        return leido

    async def obtener_conexion(tenant, conexion_id):
        return conexion

    monkeypatch.setattr(esquema, "leer", leer)
    monkeypatch.setattr(esquema.conexiones, "obtener", obtener_conexion)
    assert await OBTENER_ESQUEMA(TENANT, "c1") == leido
    assert await OBTENER_ESQUEMA(TENANT, "c1") == leido  # desde la caché
    assert await OBTENER_ESQUEMA(TENANT, "c1", refrescar=True) == leido
    assert len(lecturas) == 2

    def sin_master(c, base="master"):
        raise pyodbc.OperationalError("08001", "sin red")

    monkeypatch.setattr(esquema, "_abrir", sin_master)
    with pytest.raises(ErrorDominio) as error:
        LEER_ESQUEMA(conexion)
    assert error.value.codigo == "CON_DB_000"


# --- API ----------------------------------------------------------------------------


async def test_api_contexto(ctx):
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    dba = await _navegador(ctx, "DBA")
    consulta = await _navegador(ctx, "CONSULTA")

    r = dba.get("/api/contexto/conexiones")
    assert r.json()["puede_editar"] is True and r.json()["conexiones"][0]["id"] == conexion.id
    assert consulta.get("/api/contexto/conexiones").json()["puede_editar"] is False
    assert dba.get(f"/api/contexto/conexiones/{conexion.id}/esquema").json()["bases"] == ESQUEMA

    sensible = {
        "conexion_id": conexion.id,
        "base": "Ventas",
        "tabla": "dbo.clientes",
        "columnas": ["cuit"],
        "roles_consulta": ["DBA"],
        "roles_ven_columnas": [],
        "doble_confirmacion": True,
    }
    creada = dba.post("/api/contexto/sensibles", json=sensible, headers=CABECERA)
    assert creada.status_code == 201
    id_ = creada.json()["id"]
    assert (
        dba.put(f"/api/contexto/sensibles/{id_}", json=sensible, headers=CABECERA).status_code
        == 200
    )
    assert (
        consulta.post("/api/contexto/sensibles", json=sensible, headers=CABECERA).status_code == 403
    )

    termino = {
        "conexion_id": conexion.id,
        "base": "Ventas",
        "termino": "cliente",
        "entidad": "dbo.clientes",
        "sinonimos": ["cte"],
    }
    assert dba.post("/api/contexto/terminos", json=termino, headers=CABECERA).status_code == 201
    regla = {
        "descripcion": "Sin DELETE",
        "acciones": ["DELETE"],
        "efecto": "bloquear",
        "desde": "18:00",
        "hasta": "09:00",
    }
    regla_id = dba.post("/api/contexto/reglas", json=regla, headers=CABECERA).json()["id"]
    assert (
        dba.put(
            f"/api/contexto/reglas/{regla_id}", json={**regla, "activa": False}, headers=CABECERA
        ).json()["activa"]
        is False
    )

    listado = consulta.get(f"/api/contexto/conexiones/{conexion.id}?base=Ventas").json()
    assert (len(listado["sensibles"]), len(listado["terminos"]), len(listado["reglas"])) == (
        1,
        1,
        1,
    )
    assert listado["reglas"][0]["desde"] == "18:00"
    assert dba.delete(f"/api/contexto/reglas/{regla_id}", headers=CABECERA).status_code == 204
    assert dba.delete(f"/api/contexto/sensibles/{id_}", headers=CABECERA).status_code == 204
    assert dba.delete(f"/api/contexto/terminos/{id_}", headers=CABECERA).status_code == 404
