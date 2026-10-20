import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from datetime import time as hora

import pyodbc
import pytest

from app.core.errores import ErrorDominio
from app.modulos.conexiones import servicios as conexiones
from app.modulos.sandbox import cliente, programador, repositorios, servicios
from tests import contexto_falso
from tests.test_conexiones import (
    CABECERA,
    TENANT,
    _actor,
    _datos,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)


def test_clave_y_lotes():
    from app.modulos.conexiones.entidades import Conexion

    conexion = Conexion("c", "t", "A", "SQL01.Acme.Local", None, None, "DEV", "u", "")
    clave = servicios.clave_de(TENANT, conexion)
    assert clave.startswith("acme-sql01-acme-local-") and len(clave) <= 60
    largo = replace(conexion, host="x" * 200)
    assert len(servicios.clave_de(TENANT, largo)) <= 60
    assert servicios.lotes_de("SELECT 1\ngo\n  GO  \nSELECT 2\nGOTO x") == [
        "SELECT 1",
        "SELECT 2\nGOTO x",
    ]


PLAN = (
    '<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan"><BatchSequence>'
    '<Batch><Statements><StmtSimple StatementEstRows="120" StatementSubTreeCost="0.75">'
    '<QueryPlan><RelOp PhysicalOp="Clustered Index Scan"/></QueryPlan></StmtSimple>'
    "</Statements></Batch></BatchSequence></ShowPlanXML>"
)


def test_resumen_estimado():
    assert servicios._resumen_estimado(["<roto", PLAN]) == {
        "filas_estimadas": 120,
        "costo": 0.75,
        "operador": "Clustered Index Scan",
    }


def test_ultimo_horario_por_zona():
    from app.modulos.conexiones.entidades import Conexion

    conexion = Conexion("c", "t", "A", "h", None, None, "DEV", "u", "")
    # 10:00 UTC = 07:00 en Buenos Aires: la última de las 03:00 fue hoy a las 06:00 UTC.
    assert programador.ultimo_horario(conexion, datetime(2026, 10, 20, 10, 0, tzinfo=UTC)) == (
        datetime(2026, 10, 20, 6, 0)
    )
    # 05:00 UTC = 02:00 en Buenos Aires: todavía corresponde la de ayer.
    assert programador.ultimo_horario(conexion, datetime(2026, 10, 20, 5, 0, tzinfo=UTC)) == (
        datetime(2026, 10, 19, 6, 0)
    )
    otra = replace(conexion, sandbox_sincronizacion=hora(22, 30), zona_horaria="UTC")
    assert programador.ultimo_horario(otra, datetime(2026, 10, 20, 23, 0, tzinfo=UTC)) == (
        datetime(2026, 10, 20, 22, 30)
    )


# --- Servicios con el servicio Sandbox y los repositorios simulados -----------------


@pytest.fixture
def sbx(entorno, monkeypatch):  # noqa: F811
    filas: dict[str, dict] = {}
    bitacora: dict[str, dict] = {}
    llamadas = []
    avisos, transacciones = [], []

    def obtener(base, tenant_id, clave):
        return filas.get(clave)

    def guardar(base, tenant_id, clave, host, estado, bases=None, error=None, sincronizado=False):
        actual = filas.get(clave, {"bases": [], "sincronizado_en": None})
        filas[clave] = {
            "clave": clave,
            "host": host,
            "estado": estado,
            "bases": bases if bases is not None else actual["bases"],
            "error": error,
            "sincronizado_en": datetime.now(UTC).replace(tzinfo=None)
            if sincronizado
            else actual["sincronizado_en"],
        }

    async def registrar(base, documento):
        registro_id = f"{len(bitacora) + 1:024x}"
        bitacora[registro_id] = {**documento, "_id": registro_id, "ts": datetime.now(UTC)}
        return registro_id

    async def obtener_registro(base, tenant_id, registro_id):
        return bitacora.get(registro_id)

    async def listar(base, tenant_id, filtros, limite=50):
        return [r for r in bitacora.values() if all(r.get(k) == v for k, v in filtros.items() if v)]

    async def marcar_promovida(base, registro_id, promocion):
        bitacora[registro_id]["promocion"] = promocion

    async def estado_cliente(clave):
        return {"existe": True, "estado": "running"}

    async def sincronizar_cliente(clave, origen, bases):
        llamadas.append(("sincronizar", clave, origen["servidor"], bases))
        return [
            {
                "base": b,
                "estado": "ok" if b != "ROTA" else "error",
                "error": None if b != "ROTA" else "x",
            }
            for b in bases
        ]

    async def alineadas(clave, origen, bases):
        return dict.fromkeys(bases, True)

    async def ejecutar_cliente(clave, base, lotes, solo_sintaxis):
        llamadas.append(("ejecutar", base, lotes, solo_sintaxis))
        return {
            "estado": "ok",
            "error": None,
            "duracion_ms": 5,
            "filas": 2,
            "plan": {"operador": "Scan", "costo": 1},
            "plan_xml": [],
            "muestra": None,
            "mensajes": [],
        }

    async def avisar(tenant, roles, titulo, cuerpo, ruta):
        avisos.append(cuerpo)

    async def registrar_transaccion(base, evento):
        transacciones.append(evento)

    for nombre, funcion in (
        ("obtener", obtener),
        ("guardar", guardar),
        ("registrar", registrar),
        ("obtener_registro", obtener_registro),
        ("listar", listar),
        ("marcar_promovida", marcar_promovida),
    ):
        monkeypatch.setattr(repositorios, nombre, funcion)
    monkeypatch.setattr(cliente, "estado", estado_cliente)
    monkeypatch.setattr(cliente, "sincronizar", sincronizar_cliente)
    monkeypatch.setattr(cliente, "alineadas", alineadas)
    monkeypatch.setattr(cliente, "ejecutar", ejecutar_cliente)
    monkeypatch.setattr(servicios, "_bases_de_usuario", lambda conexion: ["ERP", "ROTA"])
    monkeypatch.setattr(servicios.notificaciones, "avisar", avisar)
    monkeypatch.setattr(servicios.auditoria, "registrar_transaccion", registrar_transaccion)
    contexto = contexto_falso.instalar(monkeypatch)
    versiones = []

    async def guardar_version(base, documento):
        versiones.append(documento)
        return len(versiones)

    async def snapshot(conexion, base):
        return None

    monkeypatch.setattr(servicios.historico.repositorios, "guardar", guardar_version)
    monkeypatch.setattr(servicios.historico, "_snapshot", snapshot)
    return {
        "versiones": versiones,
        "contexto": contexto,
        **entorno,
        "filas": filas,
        "bitacora": bitacora,
        "llamadas": llamadas,
        "avisos": avisos,
        "transacciones": transacciones,
    }


async def _terminar():
    while servicios._tareas:
        await asyncio.sleep(0.01)


async def test_sincronizar_ejecutar_y_promover(sbx, monkeypatch):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="PROD"))
    dba = _actor()

    assert (await servicios.estado(TENANT, conexion.id))["estado"] == "sin_crear"
    # Mientras la primera sincronización sigue en curso, la segunda no se repite.
    liberar = asyncio.Event()
    sincronizar_real = cliente.sincronizar

    async def sincronizar_lento(*args):
        await liberar.wait()
        return await sincronizar_real(*args)

    monkeypatch.setattr(cliente, "sincronizar", sincronizar_lento)
    await servicios.sincronizar(TENANT, dba, conexion.id)
    await servicios.sincronizar(TENANT, dba, conexion.id)
    liberar.set()
    await _terminar()
    estado = await servicios.estado(TENANT, conexion.id)
    assert estado["estado"] == "listo" and "ROTA: x" in estado["error"]
    assert [c[0] for c in sbx["llamadas"]] == ["sincronizar"]
    assert sbx["eventos"][-1][0] == "sandbox.sincronizado"

    for base, sql, codigo in (("ERP", " ", "USR_VAL_002"), ("ROTA", "SELECT 1", "SBX_SNP_001")):
        with pytest.raises(ErrorDominio) as error:
            await servicios.ejecutar(TENANT, dba, conexion.id, base, sql, False)
        assert error.value.codigo == codigo

    assert (await servicios.ejecutar(TENANT, dba, conexion.id, "ERP", "SELECT 1", True))[
        "estado"
    ] == "ok"
    resultado = await servicios.ejecutar(
        TENANT, dba, conexion.id, "ERP", "UPDATE t\nGO\nSELECT 1", False
    )
    assert resultado["estado"] == "ok" and sbx["llamadas"][-1][2] == ["UPDATE t", "SELECT 1"]
    assert [r["id"] for r in await servicios.historial(TENANT, None, None, "ok")] == [
        resultado["id"]
    ]
    assert (await servicios.detalle(TENANT, resultado["id"]))["sql"].startswith("UPDATE")
    with pytest.raises(ErrorDominio):
        await servicios.detalle(TENANT, "no-existe")

    # Estructura vieja: aviso SBX_SNP_000.
    clave = servicios.clave_de(TENANT, conexion)
    sbx["filas"][clave]["sincronizado_en"] -= timedelta(days=2)
    assert (await servicios.ejecutar(TENANT, dba, conexion.id, "ERP", "SELECT 1", False))[
        "avisos"
    ] == ["SBX_SNP_000"]
    assert (await servicios.estado(TENANT, conexion.id))["desactualizado"] is True

    monkeypatch.setattr(
        servicios,
        "_plan_estimado",
        lambda c, b, lotes: {"filas_estimadas": 7, "costo": 1.0, "operador": "Seek"},
    )
    previa = await servicios.previa(TENANT, dba, resultado["id"])
    assert previa["real"]["filas_estimadas"] == 7 and previa["ambiente"] == "PROD"
    with pytest.raises(ErrorDominio) as error:
        await servicios.previa(TENANT, _actor("ADMINISTRADOR"), resultado["id"])
    assert error.value.codigo == "USR_PER_000"
    with pytest.raises(ErrorDominio) as error:
        await servicios.promover(TENANT, dba, resultado["id"])
    assert error.value.codigo == "AMB_VAL_000"

    monkeypatch.setattr(servicios, "_ejecutar_real", lambda c, b, lotes: 7)
    con_factor = _actor(segundo_factor_en=time.time())
    assert await servicios.promover(TENANT, con_factor, resultado["id"]) == {
        "estado": "exitosa",
        "filas": 7,
    }
    assert sbx["transacciones"][-1].detalle["promocion"] is True and "7 filas" in sbx["avisos"][-1]
    with pytest.raises(ErrorDominio) as error:
        await servicios.promover(TENANT, con_factor, resultado["id"])
    assert error.value.codigo == "SBX_PRM_000"

    # Falla en la instancia real: se informa y no queda promovida.
    otro = await servicios.ejecutar(TENANT, dba, conexion.id, "ERP", "DELETE t", False)

    def fallar(c, b, lotes):
        raise ErrorDominio("SQL_EXE_000", "FK violada")

    monkeypatch.setattr(servicios, "_ejecutar_real", fallar)
    with pytest.raises(ErrorDominio) as error:
        await servicios.promover(TENANT, con_factor, otro["id"])
    assert error.value.codigo == "SQL_EXE_000" and "FK violada" in sbx["avisos"][-1]
    assert sbx["bitacora"][otro["id"]]["promocion"] is None


async def test_promover_destructiva_y_reglas(sbx, monkeypatch):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    dba = _actor()
    await servicios.sincronizar(TENANT, dba, conexion.id)
    await _terminar()
    monkeypatch.setattr(servicios, "_plan_estimado", lambda c, b, lotes: {"filas_estimadas": 1})
    monkeypatch.setattr(servicios, "_ejecutar_real", lambda c, b, lotes: 1)

    # En DEV un UPDATE no pide segundo factor; un TRUNCATE sí (destructiva).
    update = await servicios.ejecutar(TENANT, dba, conexion.id, "ERP", "UPDATE t SET a = 1", False)
    assert (await servicios.previa(TENANT, dba, update["id"]))["segundo_factor"] is False
    assert (await servicios.promover(TENANT, dba, update["id"]))["estado"] == "exitosa"
    truncate = await servicios.ejecutar(TENANT, dba, conexion.id, "ERP", "TRUNCATE TABLE t", False)
    assert (await servicios.previa(TENANT, dba, truncate["id"]))["segundo_factor"] is True
    with pytest.raises(ErrorDominio) as error:
        await servicios.promover(TENANT, dba, truncate["id"])
    assert error.value.codigo == "AMB_VAL_000"

    # Un DDL promovido queda en el histórico de schema: quién lo escribió y quién lo autorizó.
    tabla = await servicios.ejecutar(
        TENANT, dba, conexion.id, "ERP", "CREATE TABLE x (a INT)", False
    )
    assert sbx["versiones"] == []
    await servicios.promover(TENANT, dba, tabla["id"])
    [version] = sbx["versiones"]
    assert (version["origen"], version["referencia"], version["base"]) == (
        "promocion",
        tabla["id"],
        "ERP",
    )
    assert version["usuario"] == version["autorizado_por"] == dba.email

    # Una regla de bloqueo sobre DDL impide promover; las de advertencia se muestran.
    sbx["contexto"]["r1"] = {
        "tipo": "regla",
        "tenant_id": str(TENANT.id),
        "descripcion": "Sin DDL",
        "acciones": ["DDL"],
        "efecto": "bloquear",
        "prioridad": 1,
        "conexion_id": None,
        "base": None,
        "ambientes": [],
        "tablas": [],
        "desde": None,
        "hasta": None,
        "activa": True,
    }
    sbx["contexto"]["r2"] = {**sbx["contexto"]["r1"], "descripcion": "Ojo", "efecto": "advertir"}
    alter = await servicios.ejecutar(
        TENANT, dba, conexion.id, "ERP", "ALTER TABLE t ADD b INT", False
    )
    previa = await servicios.previa(TENANT, dba, alter["id"])
    assert [r["descripcion"] for r in previa["reglas_bloqueo"]] == ["Sin DDL"]
    assert [r["descripcion"] for r in previa["advertencias"]] == ["Ojo"]
    with pytest.raises(ErrorDominio) as error:
        await servicios.promover(TENANT, dba, alter["id"])
    assert error.value.codigo == "CTX_REG_000" and error.value.datos["reglas"][0]["id"] == "r1"


async def test_sincronizacion_fallida_y_servicio_caido(sbx, monkeypatch):
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))

    async def caido(*_):
        raise ErrorDominio("SBX_DCK_000", "sin servicio")

    monkeypatch.setattr(cliente, "sincronizar", caido)
    monkeypatch.setattr(cliente, "estado", caido)
    await servicios.sincronizar(TENANT, None, conexion.id)
    await _terminar()
    estado = await servicios.estado(TENANT, conexion.id)
    assert estado["estado"] == "error" and estado["contenedor"] == {"disponible": False}


def test_ejecucion_real_y_plan_estimado(sbx, monkeypatch):
    class Cursor:
        def __init__(self, conexion):
            self.conexion = conexion
            self.description = None
            self.rowcount = 0

        def execute(self, sql):
            if sql == "FALLA":
                raise pyodbc.ProgrammingError("23000", "FK")
            self.conexion.ejecutados.append(sql)
            self.description = [("plan",)] if "SHOWPLAN" not in sql and self.conexion.plan else None
            self.rowcount = 3
            return self

        def fetchall(self):
            return [(PLAN,)]

        def nextset(self):
            return False

    class Conexion:
        def __init__(self, plan=False):
            self.plan = plan
            self.ejecutados = []
            self.commits = self.rollbacks = 0
            self.autocommit = True

        def cursor(self):
            return Cursor(self)

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            pass

    from app.core import cifrado
    from app.modulos.conexiones.entidades import Conexion as Modelo

    modelo = Modelo("c", "t", "A", "h", None, None, "PROD", "u", cifrado.cifrar("x"))
    real = Conexion()
    monkeypatch.setattr(servicios.sqlserver, "abrir_externo", lambda *a, **k: real)
    assert servicios._ejecutar_real(modelo, "ERP", ["UPDATE t"]) == 3 and real.commits == 1
    with pytest.raises(ErrorDominio):
        servicios._ejecutar_real(modelo, "ERP", ["FALLA"])
    assert real.rollbacks == 1

    con_plan = Conexion(plan=True)
    monkeypatch.setattr(servicios.sqlserver, "abrir_externo", lambda *a, **k: con_plan)
    assert servicios._plan_estimado(modelo, "ERP", ["UPDATE t"])["filas_estimadas"] == 120
    with pytest.raises(ErrorDominio):
        servicios._plan_estimado(modelo, "ERP", ["FALLA"])


async def test_programador_sincroniza_lo_vencido(sbx, monkeypatch, redis_falso):
    primera = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    await conexiones.crear(
        TENANT, _actor(), _datos(ambiente="DEV", alias="MISMO-HOST", puerto=1450)
    )
    monkeypatch.setattr(
        programador.tenants, "listar", lambda: [TENANT, replace(TENANT, estado="baja")]
    )
    sincronizadas = []

    async def sincronizar(tenant, actor, conexion_id):
        sincronizadas.append(conexion_id)

    monkeypatch.setattr(programador.servicios, "sincronizar", sincronizar)
    p = programador.Programador()
    assert await p._tomar_candado() and await p._tomar_candado()
    await p.revisar(datetime.now(UTC))
    assert len(sincronizadas) == 1  # mismo servidor: un solo Sandbox

    clave = servicios.clave_de(TENANT, await conexiones.obtener(TENANT, primera.id))
    sbx["filas"][clave] = {
        "estado": "listo",
        "sincronizado_en": datetime.now(UTC).replace(tzinfo=None),
        "bases": [],
    }
    await p.revisar(datetime.now(UTC))
    assert len(sincronizadas) == 1


async def test_api_sandbox(sbx, monkeypatch):
    monkeypatch.setattr(servicios, "_plan_estimado", lambda c, b, lotes: {"filas_estimadas": 1})
    monkeypatch.setattr(servicios, "_ejecutar_real", lambda c, b, lotes: 1)
    conexion = await conexiones.crear(TENANT, _actor(), _datos(ambiente="DEV"))
    navegador = await _navegador(sbx, "DBA")

    assert navegador.get(f"/api/sandbox/conexiones/{conexion.id}").json()["estado"] == "sin_crear"
    assert (
        navegador.post(
            f"/api/sandbox/conexiones/{conexion.id}/sincronizar", headers=CABECERA
        ).status_code
        == 202
    )
    # La sincronización corre en el loop del cliente de prueba: se espera consultando el estado.
    for _ in range(200):
        estado = navegador.get(f"/api/sandbox/conexiones/{conexion.id}").json()["estado"]
        if estado != "sincronizando":
            break
        await asyncio.sleep(0.01)
    assert estado == "listo"
    ejecucion = navegador.post(
        "/api/sandbox/ejecutar",
        json={"conexion_id": conexion.id, "base": "ERP", "sql": "SELECT 1"},
        headers=CABECERA,
    ).json()
    historial = navegador.get("/api/sandbox/historial").json()
    assert (
        historial["puede_promover"] is True and historial["registros"][0]["id"] == ejecucion["id"]
    )
    assert navegador.get(f"/api/sandbox/historial/{ejecucion['id']}").json()["base"] == "ERP"
    previa = navegador.post(f"/api/sandbox/historial/{ejecucion['id']}/previa", headers=CABECERA)
    assert previa.json()["real"] == {"filas_estimadas": 1}
    promovida = navegador.post(
        f"/api/sandbox/historial/{ejecucion['id']}/promover", headers=CABECERA
    )
    assert promovida.json() == {"estado": "exitosa", "filas": 1}
    assert navegador.get("/sandbox").status_code == 200

    consulta = await _navegador(sbx, "CONSULTA")
    assert consulta.get("/api/sandbox/historial").json()["codigo"] == "USR_PER_000"
