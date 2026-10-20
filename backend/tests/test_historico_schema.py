from datetime import UTC, datetime

import pytest

from app.core.errores import ErrorDominio
from app.modulos.conexiones import servicios as conexiones
from app.modulos.contexto_negocio import esquema
from app.modulos.historico_schema import captura, repositorios, servicios
from tests.test_conexiones import (
    TENANT,
    _actor,
    _datos,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)


class _Cursor:
    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.filas = []

    def execute(self, sql):
        self.filas = next(filas for clave, filas in self.respuestas.items() if clave in sql)
        return self

    def fetchall(self):
        return self.filas


class _Conexion:
    def __init__(self, respuestas):
        self.respuestas = respuestas

    def cursor(self):
        return _Cursor(self.respuestas)


RESPUESTAS = {
    "default_constraints": [
        ("dbo", "clientes", "id", "int", 4, 10, 0, False, True, None, 1),
        ("dbo", "clientes", "nombre", "nvarchar", 200, 0, 0, True, False, None, 2),
        ("dbo", "clientes", "saldo", "decimal", 9, 12, 2, False, False, "((0))", 3),
        ("dbo", "pedidos", "cliente_id", "int", 4, 10, 0, False, False, None, 1),
    ],
    "sys.indexes": [
        ("dbo", "clientes", "PK_clientes", True, True, "CLUSTERED", "id", False, False, 1, 1, None),
        (
            "dbo",
            "clientes",
            "IX_nombre",
            False,
            False,
            "NONCLUSTERED",
            "nombre",
            True,
            False,
            1,
            1,
            "([nombre] IS NOT NULL)",
        ),
        (
            "dbo",
            "clientes",
            "IX_nombre",
            False,
            False,
            "NONCLUSTERED",
            "saldo",
            False,
            True,
            0,
            2,
            "([nombre] IS NOT NULL)",
        ),
    ],
    "sys.foreign_keys": [
        (
            "dbo",
            "pedidos",
            "FK_pedidos_clientes",
            "cliente_id",
            "dbo",
            "clientes",
            "id",
            "CASCADE",
            1,
        ),
    ],
    "sys.sql_modules": [
        ("V ", "dbo", "v_activos", "CREATE VIEW dbo.v_activos AS   \r\nSELECT 1 AS x  "),
        ("P ", "dbo", "sp_x", "CREATE PROCEDURE dbo.sp_x AS SELECT 1"),
    ],
}


def test_captura_del_catalogo():
    objetos = captura.capturar(_Conexion(RESPUESTAS))
    assert set(objetos) == {
        "tabla:dbo.clientes",
        "tabla:dbo.pedidos",
        "indice:dbo.clientes.IX_nombre",
        "vista:dbo.v_activos",
        "procedimiento:dbo.sp_x",
    }
    clientes = objetos["tabla:dbo.clientes"]["definicion"]
    assert "[id] int IDENTITY NOT NULL" in clientes
    assert "[nombre] nvarchar(100) NULL" in clientes
    assert "[saldo] decimal(12, 2) NOT NULL DEFAULT ((0))" in clientes
    assert "CONSTRAINT [PK_clientes] PRIMARY KEY CLUSTERED ([id])" in clientes
    assert objetos["indice:dbo.clientes.IX_nombre"]["definicion"] == (
        "CREATE NONCLUSTERED INDEX [IX_nombre] ON [dbo].[clientes] ([nombre] DESC)"
        " INCLUDE ([saldo]) WHERE ([nombre] IS NOT NULL);"
    )
    assert (
        "CONSTRAINT [FK_pedidos_clientes] FOREIGN KEY ([cliente_id]) REFERENCES [dbo].[clientes]"
        " ([id]) ON DELETE CASCADE" in objetos["tabla:dbo.pedidos"]["definicion"]
    )
    # Los espacios finales que agrega el motor no cuentan como cambio.
    assert (
        objetos["vista:dbo.v_activos"]["definicion"]
        == "CREATE VIEW dbo.v_activos AS\nSELECT 1 AS x"
    )


def test_diferencias():
    antes = {
        "tabla:dbo.a": {"tipo": "tabla", "nombre": "dbo.a", "definicion": "A1"},
        "tabla:dbo.b": {"tipo": "tabla", "nombre": "dbo.b", "definicion": "B"},
        "vista:dbo.v": {"tipo": "vista", "nombre": "dbo.v", "definicion": "V"},
    }
    despues = {
        "tabla:dbo.a": {"tipo": "tabla", "nombre": "dbo.a", "definicion": "A2"},
        "tabla:dbo.b": {"tipo": "tabla", "nombre": "dbo.b", "definicion": "B"},
        "indice:dbo.a.ix": {"tipo": "indice", "nombre": "dbo.a.ix", "definicion": "I"},
    }
    cambios = captura.diferencias(antes, despues)
    assert [(c["operacion"], c["nombre"]) for c in cambios] == [
        ("agregado", "dbo.a.ix"),
        ("modificado", "dbo.a"),
        ("eliminado", "dbo.v"),
    ]
    assert cambios[1]["antes"] == "A1" and cambios[1]["despues"] == "A2"
    assert captura.diferencias(antes, antes) == []


@pytest.fixture
def historia(entorno, monkeypatch):  # noqa: F811
    versiones: list[dict] = []
    estados: dict[str, dict | None] = {}

    async def guardar(base, documento):
        numero = 1 + sum(
            1
            for v in versiones
            if (v["conexion_id"], v["base"]) == (documento["conexion_id"], documento["base"])
        )
        versiones.append({**documento, "version": numero, "ts": datetime(2026, 10, 20, tzinfo=UTC)})
        return numero

    async def listar(base, tenant_id, conexion_id, base_datos):
        return sorted(
            (v for v in versiones if (v["conexion_id"], v["base"]) == (conexion_id, base_datos)),
            key=lambda v: -v["version"],
        )

    async def obtener(base, tenant_id, conexion_id, base_datos, version):
        return next(
            (
                v
                for v in versiones
                if (v["conexion_id"], v["base"], v["version"]) == (conexion_id, base_datos, version)
            ),
            None,
        )

    async def bases_con_versiones(base, tenant_id, conexion_id):
        return sorted({v["base"] for v in versiones if v["conexion_id"] == conexion_id})

    async def snapshot(conexion, base):
        return estados.get(base)

    async def esquema_falso(tenant, conexion_id, refrescar=False):
        return {"Ventas": [], "Compras": []}

    monkeypatch.setattr(repositorios, "guardar", guardar)
    monkeypatch.setattr(repositorios, "listar", listar)
    monkeypatch.setattr(repositorios, "obtener", obtener)
    monkeypatch.setattr(repositorios, "bases_con_versiones", bases_con_versiones)
    monkeypatch.setattr(servicios, "_snapshot", snapshot)
    monkeypatch.setattr(esquema, "obtener", esquema_falso)
    return {**entorno, "versiones": versiones, "estados": estados}


def _objeto(nombre, definicion, tipo="tabla"):
    return {f"{tipo}:{nombre}": {"tipo": tipo, "nombre": nombre, "definicion": definicion}}


async def test_registro_de_versiones_y_comparacion(historia):
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    estados = historia["estados"]

    # Una lectura o una modificación de datos no generan versión.
    assert (
        await servicios.preparar(conexion, "Ventas", "UPDATE dbo.t SET a = 1 WHERE b = 1") is None
    )
    assert await servicios.preparar(conexion, None, "ALTER TABLE t ADD c INT") is None

    estados["Ventas"] = _objeto("dbo.t", "v1")
    previa = await servicios.preparar(
        conexion, "Ventas", "SELECT 1\nGO\nALTER TABLE dbo.t ADD c INT"
    )
    estados["Ventas"] = _objeto("dbo.t", "v2") | _objeto("dbo.t.ix", "i", "indice")
    assert await servicios.registrar(
        TENANT, previa, "ALTER ...", "dev@acme.com", "promocion", "r1", "dba@acme.com"
    ) == [1]

    # Un DDL que no cambia la estructura no genera versión.
    previa = await servicios.preparar(conexion, "Ventas", "ALTER INDEX ix ON dbo.t REBUILD")
    assert (
        await servicios.registrar(TENANT, previa, "ALTER INDEX", "dba@acme.com", "workaround", "7")
        == []
    )
    assert await servicios.registrar(TENANT, None, "x", "u", "promocion", "r") == []

    # Si no se pudo leer la estructura, queda el DDL sin diff.
    estados["Ventas"] = None
    previa = await servicios.preparar(conexion, "Ventas", "DROP VIEW dbo.v")
    assert await servicios.registrar(
        TENANT, previa, "DROP VIEW dbo.v", "dba@acme.com", "workaround", "8"
    ) == [2]

    listado = await servicios.versiones(TENANT, conexion.id, "Ventas")
    assert [v["version"] for v in listado["versiones"]] == [2, 1]
    v2, v1 = listado["versiones"]
    assert v1["resumen"] == {"agregado": 1, "eliminado": 0, "modificado": 1}
    assert v1["usuario"] == "dev@acme.com" and v1["autorizado_por"] == "dba@acme.com"
    assert v2["sin_diff"] is True and v2["cambios"] == 0
    detalle = await servicios.detalle(TENANT, conexion.id, "Ventas", 1)
    assert [c["operacion"] for c in detalle["detalle"]] == ["agregado", "modificado"]
    with pytest.raises(ErrorDominio) as error:
        await servicios.detalle(TENANT, conexion.id, "Ventas", 9)
    assert error.value.codigo == "SCH_SNP_000"

    # Comparación con el estado actual y entre versiones.
    estados["Ventas"] = _objeto("dbo.t", "v3")
    comparacion = await servicios.comparar(TENANT, conexion.id, "Ventas", 1, servicios.ACTUAL)
    assert comparacion["resumen"] == {"agregado": 0, "eliminado": 1, "modificado": 1}
    script = servicios.como_script(comparacion)
    assert "-- MODIFICADO: tabla dbo.t" in script and "v3" in script
    for desde, hasta in ((1, 1), (1, 2)):
        with pytest.raises(ErrorDominio):
            await servicios.comparar(TENANT, conexion.id, "Ventas", desde, hasta)


async def test_cambios_en_varias_bases(historia):
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    historia["estados"].update(Ventas=_objeto("dbo.a", "1"), Compras=_objeto("dbo.b", "1"))
    previa = await servicios.preparar(conexion, "Ventas", "ALTER TABLE Compras.dbo.b ADD x INT")
    assert set(previa.antes) == {"Ventas", "Compras"}
    historia["estados"]["Compras"] = _objeto("dbo.b", "2")
    assert await servicios.registrar(TENANT, previa, "ALTER", "u", "promocion", "r") == [1]
    assert historia["versiones"][0]["base"] == "Compras"


async def test_api_schema(historia):
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    historia["estados"]["Ventas"] = _objeto("dbo.t", "1")
    previa = await servicios.preparar(conexion, "Ventas", "CREATE TABLE dbo.u (a INT)")
    historia["estados"]["Ventas"] = _objeto("dbo.t", "1") | _objeto("dbo.u", "2")
    await servicios.registrar(
        TENANT, previa, "CREATE TABLE dbo.u (a INT)", "dba@acme.com", "promocion", "r"
    )

    navegador = await _navegador(historia, "CONSULTA")
    assert navegador.get("/api/schema/conexiones").json()["conexiones"][0]["id"] == conexion.id
    assert navegador.get(f"/api/schema/conexiones/{conexion.id}/bases").json()["bases"] == [
        "Compras",
        "Ventas",
    ]
    ruta = f"/api/schema/conexiones/{conexion.id}/bases/Ventas"
    assert navegador.get(f"{ruta}/versiones").json()["versiones"][0]["cambios"] == 1
    assert navegador.get(f"{ruta}/versiones/1").json()["detalle"][0]["nombre"] == "dbo.u"
    historia["estados"]["Ventas"] = {}
    assert navegador.get(f"{ruta}/comparar?desde=1").json()["resumen"]["eliminado"] == 2
    descarga = navegador.get(f"{ruta}/comparar.sql?desde=1&hasta=actual")
    assert descarga.status_code == 200 and "ELIMINADO" in descarga.text
    assert "Línea de" in navegador.get("/schema").text
