import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.core.errores import ErrorDominio
from app.identidad.entidades import Usuario
from app.modulos.buenas_practicas import analisis_ia, chequeos, repositorios, servicios
from app.modulos.chat_ia import motor_ia
from app.modulos.conexiones import servicios as conexiones
from app.modulos.contexto_negocio import esquema
from tests.test_conexiones import (
    CABECERA,
    TENANT,
    _actor,
    _datos,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)

# --- Chequeos -------------------------------------------------------------------------


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
    timeout = 0

    def __init__(self, respuestas):
        self.respuestas = respuestas

    def cursor(self):
        return _Cursor(self.respuestas)


RESPUESTAS = {
    "dm_exec_query_stats": [
        ("SELECT *\n  FROM dbo.pedidos WHERE x = 1", 120, 1500.0, 1800.2, 90000)
    ],
    "dm_db_missing_index_details": [
        ("dbo", "pedidos", "[cliente_id], [estado]", "[fecha]", "[total]", 85.3, 400),
        ("dbo", "facturas", "[numero]", None, None, 12.0, 3),
    ],
    "dm_db_index_physical_stats": [
        ("dbo", "pedidos", "IX_fecha", 45.2, 5000),
        ("dbo", "clientes", "IX_nombre", 12.0, 1200),
    ],
    "i.type = 0": [("dbo", "eventos")],
    "is_primary_key = 1)\n": [("dbo", "logs", 1)],
    "sys.foreign_keys": [("dbo", "pedidos", "FK_pedidos_clientes", "cliente_id")],
    "'text', 'ntext', 'image'": [("dbo", "notas", "cuerpo", "ntext")],
}


def test_chequeos():
    conexion = _Conexion(RESPUESTAS)
    hallazgos = chequeos.ejecutar(conexion, list(chequeos.TIPOS))
    assert conexion.timeout == chequeos.TIEMPO_MAXIMO_SEGUNDOS
    por_tipo = {}
    for h in hallazgos:
        por_tipo.setdefault(h["tipo"], []).append(h)

    [consulta] = por_tipo["consultas"]
    assert (
        consulta["severidad"] == "alta"
        and consulta["objeto"] == "SELECT * FROM dbo.pedidos WHERE x = 1"
    )
    assert consulta["datos"]["cpu_promedio_ms"] == 1500.0 and consulta["sql"] is None

    indice, menor = por_tipo["indices"]
    assert indice["sql"] == (
        "CREATE NONCLUSTERED INDEX [IX_pedidos_cliente_id_estado_fecha] ON [dbo].[pedidos]"
        " ([cliente_id], [estado], [fecha]) INCLUDE ([total]);"
    )
    assert (indice["severidad"], menor["severidad"]) == ("alta", "baja")

    rebuild, reorganize = por_tipo["fragmentacion"]
    assert rebuild["sql"] == "ALTER INDEX [IX_fecha] ON [dbo].[pedidos] REBUILD;"
    assert reorganize["sql"].endswith("REORGANIZE;") and reorganize["severidad"] == "media"

    descripciones = [h["descripcion"] for h in por_tipo["esquema"]]
    assert descripciones == [
        "Tabla sin clave primaria ni índice clustered",
        "Tabla heap: la clave primaria no es clustered",
        "Clave foránea FK_pedidos_clientes sin índice en cliente_id",
        "Tipo de dato obsoleto (ntext)",
    ]
    assert por_tipo["esquema"][3]["sql"] == (
        "ALTER TABLE [dbo].[notas] ALTER COLUMN [cuerpo] nvarchar(max);"
    )


async def test_analisis_ia_usa_el_motor_comun(monkeypatch):
    llamadas = []

    async def solicitar(instrucciones, contexto, mensajes, formato, maximo, codigo, timeout):
        llamadas.append((contexto, json.loads(mensajes[0]["content"]), formato, codigo, timeout))
        return analisis_ia.Informe(resumen="ok", sugerencias=[])

    monkeypatch.setattr(motor_ia, "solicitar", solicitar)
    hallazgos = [{"tipo": "indices", "severidad": "alta", "objeto": "dbo.t", "sql": "CREATE ..."}]
    assert (await analisis_ia.analizar("ctx", "Ventas", hallazgos, ["regla 1"])).resumen == "ok"
    contexto, pedido, formato, codigo, timeout = llamadas[0]
    assert contexto == "ctx" and formato is analisis_ia.Informe and codigo == "IA_API_000"
    assert pedido == {
        "base": "Ventas",
        "hallazgos": [{"tipo": "indices", "objeto": "dbo.t", "sql": "CREATE ..."}],
        "reglas_del_equipo": ["regla 1"],
    }
    assert timeout == analisis_ia.TIMEOUT_SEGUNDOS


# --- Servicios ------------------------------------------------------------------------


@pytest.fixture
def bp(entorno, monkeypatch):  # noqa: F811
    informes: dict[str, dict] = {}
    reglas: dict[str, dict] = {}
    eventos = []
    entorno["usuarios"].usuarios["u"] = Usuario(
        "u", str(TENANT.id), "dba@acme.com", "Dba", "DBA", "activo", None, ("DEV",)
    )

    async def crear_informe(base, documento):
        oid = ObjectId()
        informes[str(oid)] = {**documento, "_id": oid, "ts": datetime.now(UTC)}
        return str(oid)

    async def actualizar_informe(base, informe_id, campos):
        informes[informe_id].update(campos)

    async def obtener_informe(base, tenant_id, informe_id):
        return informes.get(informe_id)

    async def listar_informes(base, tenant_id, conexion_id):
        return [i for i in informes.values() if conexion_id in (None, i["conexion_id"])]

    async def listar_reglas(base, tenant_id):
        return list(reglas.values())

    async def guardar_regla(base, tenant_id, regla_id, datos):
        if regla_id is not None and regla_id not in reglas:
            return None
        nuevo = regla_id or str(ObjectId())
        reglas[nuevo] = {"_id": ObjectId(nuevo), **datos}
        return nuevo

    async def eliminar_regla(base, tenant_id, regla_id):
        return reglas.pop(regla_id, None) is not None

    for nombre, funcion in (
        ("crear_informe", crear_informe),
        ("actualizar_informe", actualizar_informe),
        ("obtener_informe", obtener_informe),
        ("listar_informes", listar_informes),
        ("listar_reglas", listar_reglas),
        ("guardar_regla", guardar_regla),
        ("eliminar_regla", eliminar_regla),
    ):
        monkeypatch.setattr(repositorios, nombre, funcion)

    async def obtener_esquema(tenant, conexion_id, refrescar=False):
        return {"Ventas": [], "Compras": []}

    async def texto(tenant, conexion):
        return "esquema y contexto"

    async def auditar(base, evento):
        eventos.append((evento.accion, evento.detalle))

    respuestas = {"ia": analisis_ia.Informe(resumen="Dos mejoras", sugerencias=[])}
    pedidos_ia = []

    async def analizar(contexto, base, hallazgos, reglas_activas):
        pedidos_ia.append((contexto, base, hallazgos, reglas_activas))
        if isinstance(respuestas["ia"], Exception):
            raise respuestas["ia"]
        return respuestas["ia"]

    monkeypatch.setattr(esquema, "obtener", obtener_esquema)
    monkeypatch.setattr(servicios.contexto, "texto_para_ia", texto)
    monkeypatch.setattr(servicios.auditoria, "registrar", auditar)
    monkeypatch.setattr(
        servicios, "_leer", lambda c, b, tipos: [{"tipo": t, "objeto": b} for t in tipos]
    )
    monkeypatch.setattr(analisis_ia, "analizar", analizar)
    return {
        **entorno,
        "informes": informes,
        "eventos": eventos,
        "respuestas": respuestas,
        "pedidos_ia": pedidos_ia,
    }


async def _terminar():
    while servicios._tareas:
        await asyncio.sleep(0.01)


def _sugerencia(**cambios):
    datos = {
        "titulo": "Crear índice",
        "tipo": "indices",
        "severidad": "alta",
        "objeto": "dbo.pedidos",
        "descripcion": "Mejora el filtro por cliente",
        "sql": "CREATE INDEX IX ON dbo.pedidos (cliente_id)",
        "indice_hallazgo": 0,
    }
    return analisis_ia.Sugerencia(**{**datos, **cambios})


async def test_solicitar_y_aplicar(bp, monkeypatch):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    await servicios.guardar_regla(TENANT, _actor(), None, "Toda tabla con PK", True)
    await servicios.guardar_regla(TENANT, _actor(), None, "Regla apagada", False)
    bp["respuestas"]["ia"] = analisis_ia.Informe(
        resumen="Dos mejoras",
        sugerencias=[_sugerencia(), _sugerencia(titulo="Revisar", severidad="baja", sql=None)],
    )

    informe = await servicios.solicitar(
        TENANT, _actor(), conexion.id, "ventas", ["esquema", "consultas", "x"]
    )
    assert informe["estado"] == "en_curso" and informe["base"] == "Ventas"
    assert informe["tipos"] == ["consultas", "esquema"]
    await _terminar()
    listo = await servicios.informe(TENANT, _actor(), informe["id"])
    assert listo["estado"] == "listo" and listo["conteo"] == {"alta": 1, "media": 0, "baja": 1}
    assert [h["tipo"] for h in listo["hallazgos"]] == ["consultas", "esquema"]
    contexto, base, _, reglas = bp["pedidos_ia"][0]
    assert (contexto, base, reglas) == ("esquema y contexto", "Ventas", ["Toda tabla con PK"])
    assert bp["eventos"][-1][0] == "buenas_practicas.analizado"

    llamadas = []

    async def ejecutar_sandbox(tenant, actor, conexion_id, base, sql, solo_sintaxis):
        llamadas.append((base, sql, solo_sintaxis))
        return {"id": "r1", "estado": "ok", "avisos": []}

    monkeypatch.setattr(servicios.sandbox, "ejecutar", ejecutar_sandbox)
    aplicada = await servicios.validar_en_sandbox(TENANT, _actor(), informe["id"], 0)
    assert aplicada["sandbox"]["registro_id"] == "r1" and llamadas[0][0] == "Ventas"
    assert bp["informes"][informe["id"]]["sugerencias"][0]["sandbox"]["estado"] == "ok"
    for actor, sugerencia, codigo in (
        (_actor("ADMINISTRADOR"), 0, "USR_PER_000"),
        (_actor(), 1, "BPR_VAL_001"),
        (_actor(), 9, "BPR_VAL_000"),
    ):
        with pytest.raises(ErrorDominio) as error:
            await servicios.validar_en_sandbox(TENANT, actor, informe["id"], sugerencia)
        assert error.value.codigo == codigo

    assert [
        i["id"] for i in await servicios.informes(TENANT, _actor("ADMINISTRADOR"), conexion.id)
    ] == [informe["id"]]
    with pytest.raises(ErrorDominio):
        await servicios.informe(TENANT, _actor(), "no-existe")


async def test_errores_del_analisis(bp, monkeypatch):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    for actor, base, tipos, codigo in (
        (_actor("CONSULTA"), "Ventas", ["esquema"], "USR_PER_000"),
        (_actor(), "Ventas", [], "USR_VAL_002"),
        (_actor(), "Otra", ["esquema"], "USR_VAL_002"),
    ):
        with pytest.raises(ErrorDominio) as error:
            await servicios.solicitar(TENANT, actor, conexion.id, base, tipos)
        assert error.value.codigo == codigo
    prod = await conexiones.crear(TENANT, _actor(), _datos(alias="PROD-01", ambiente="PROD"))
    with pytest.raises(ErrorDominio) as error:
        await servicios.solicitar(TENANT, _actor(), prod.id, "Ventas", ["esquema"])
    assert error.value.codigo == "USR_PER_000"  # ambiente no habilitado al usuario

    # Falla del motor IA: el informe queda en error con los datos medidos.
    bp["respuestas"]["ia"] = ErrorDominio("IA_API_000", "caído")
    informe = await servicios.solicitar(TENANT, _actor(), conexion.id, "Ventas", ["indices"])
    await _terminar()
    fallido = await servicios.informe(TENANT, _actor(), informe["id"])
    assert fallido["estado"] == "error" and fallido["error"] == "IA_API_000"
    assert fallido["hallazgos"] == [{"tipo": "indices", "objeto": "Ventas"}]

    def sin_permiso(c, b, tipos):
        raise ErrorDominio("MNT_DAT_000", "VIEW SERVER STATE")

    monkeypatch.setattr(servicios, "_leer", sin_permiso)
    informe = await servicios.solicitar(TENANT, _actor(), conexion.id, "Ventas", ["indices"])
    await _terminar()
    assert (await servicios.informe(TENANT, _actor(), informe["id"]))["error"] == "MNT_DAT_000"

    def inesperado(c, b, tipos):
        raise RuntimeError("x")

    monkeypatch.setattr(servicios, "_leer", inesperado)
    informe = await servicios.solicitar(TENANT, _actor(), conexion.id, "Ventas", ["indices"])
    await _terminar()
    assert (await servicios.informe(TENANT, _actor(), informe["id"]))["estado"] == "error"

    # Un análisis que nunca terminó se muestra como interrumpido.
    bp["informes"][informe["id"]].update(
        estado="en_curso", ts=datetime.now(UTC) - timedelta(hours=1)
    )
    assert (await servicios.informe(TENANT, _actor(), informe["id"]))["estado"] == "interrumpido"


async def test_reglas_del_equipo(bp):
    regla = await servicios.guardar_regla(TENANT, _actor(), None, "  Sin SELECT *  ", True)
    assert regla["texto"] == "Sin SELECT *"
    assert (await servicios.guardar_regla(TENANT, _actor(), regla["id"], "Sin SELECT *", False))[
        "activa"
    ] is False
    for actor, regla_id, texto, codigo in (
        (_actor("ADMINISTRADOR"), None, "x", "USR_PER_000"),
        (_actor(), None, " ", "USR_VAL_002"),
        (_actor(), str(ObjectId()), "x", "BPR_VAL_000"),
    ):
        with pytest.raises(ErrorDominio) as error:
            await servicios.guardar_regla(TENANT, actor, regla_id, texto, True)
        assert error.value.codigo == codigo
    assert [r["texto"] for r in await servicios.reglas(TENANT)] == ["Sin SELECT *"]
    await servicios.eliminar_regla(TENANT, _actor(), regla["id"])
    with pytest.raises(ErrorDominio):
        await servicios.eliminar_regla(TENANT, _actor(), regla["id"])
    acciones = [e[0] for e in bp["eventos"]]
    assert acciones == [
        "buenas_practicas.regla_creada",
        "buenas_practicas.regla_modificada",
        "buenas_practicas.regla_eliminada",
    ]


async def test_api_buenas_practicas(bp):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    navegador = await _navegador(bp, "DBA")
    usuario = next(
        u for u in bp["usuarios"].usuarios.values() if u.email == "dba@acme.com" and u.id != "u"
    )
    from dataclasses import replace

    bp["usuarios"].usuarios[usuario.id] = replace(usuario, ambientes=("DEV",))
    assert (
        navegador.get("/api/buenas-practicas/conexiones").json()["conexiones"][0]["id"]
        == conexion.id
    )
    assert navegador.get(f"/api/buenas-practicas/conexiones/{conexion.id}/bases").json()[
        "bases"
    ] == ["Compras", "Ventas"]
    creada = navegador.post(
        "/api/buenas-practicas/informes",
        json={"conexion_id": conexion.id, "base": "Ventas", "tipos": ["esquema"]},
        headers=CABECERA,
    )
    assert creada.status_code == 202
    resumen = navegador.get("/api/buenas-practicas").json()
    assert resumen["permisos"] == {"analizar": True, "aplicar": True, "reglas": True}
    assert resumen["informes"][0]["id"] == creada.json()["id"]
    assert navegador.get(f"/api/buenas-practicas/informes/{creada.json()['id']}").status_code == 200
    regla = navegador.post(
        "/api/buenas-practicas/reglas", json={"texto": "Sin heaps"}, headers=CABECERA
    ).json()
    assert (
        navegador.put(
            f"/api/buenas-practicas/reglas/{regla['id']}",
            json={"texto": "Sin heaps", "activa": False},
            headers=CABECERA,
        ).json()["activa"]
        is False
    )
    assert (
        navegador.delete(
            f"/api/buenas-practicas/reglas/{regla['id']}", headers=CABECERA
        ).status_code
        == 204
    )
    assert "Análisis y" in navegador.get("/buenas-practicas").text
    assert "Optimizar" in navegador.get("/monitor").text

    consulta = await _navegador(bp, "CONSULTA")
    assert consulta.get("/api/buenas-practicas").status_code == 403
