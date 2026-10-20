import itertools
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from app import cli
from app.core.errores import ErrorDominio
from app.modulos.alertas import condiciones, motor, repositorios, servicios
from app.modulos.alertas.entidades import Alerta, Condicion, Regla, ResultadoDisparo
from app.modulos.monitor import repositorios as monitor_repo
from app.modulos.monitor.entidades import Lectura, Umbral
from app.modulos.web_push import repositorios as push_repo
from app.modulos.web_push import servicios as push
from tests.test_conexiones import (
    CABECERA,
    TENANT,
    _actor,
    _datos,
    _navegador,
    entorno,  # noqa: F401 (fixture)
)

# --- Condiciones --------------------------------------------------------------------


def _lectura(**valores) -> Lectura:
    base = {"cpu": 10.0, "memoria": 10.0, "disco": 10.0, "latencia": 0.0, "sesiones": 1.0}
    return Lectura({**base, **valores})


def test_condiciones_por_umbrales():
    umbrales = {
        "memoria": Umbral("memoria", False, 1, 2),
        "sesiones": Umbral("sesiones", True, 5, 10),
    }
    resultado = condiciones.por_umbrales(
        _lectura(cpu=90.0, disco=80.0, memoria=99.0, sesiones=7.0), umbrales
    )

    por_clave = {c.clave: c for c in resultado}
    assert set(por_clave) == {"umbral:cpu", "umbral:disco", "umbral:sesiones"}
    assert por_clave["umbral:cpu"].severidad == "critical" and por_clave["umbral:cpu"].push
    assert por_clave["umbral:cpu"].titulo == "CPU por encima del umbral critical"
    assert por_clave["umbral:disco"].severidad == "warning" and not por_clave["umbral:disco"].push
    assert por_clave["umbral:disco"].descripcion == "Espacio en disco en 80%; umbral 75%."
    assert por_clave["umbral:sesiones"].umbral == 5


def test_condiciones_por_eventos():
    lectura = Lectura(
        {},
        bloqueos=[
            {"sid": 87, "bloqueador": 52, "base": "ERP", "recurso": "KEY", "espera_s": 45.0},
            {"sid": 90, "bloqueador": 52, "base": None, "recurso": None, "espera_s": 31.5},
            {"sid": 91, "bloqueador": 52, "base": "ERP", "recurso": "KEY", "espera_s": 2.0},
        ],
        bases_no_disponibles=[{"base": "CRM", "estado": "SUSPECT"}],
        logs=[{"base": "ERP", "uso": 95.0}, {"base": "CRM", "uso": 50.0}],
    )
    resultado = {c.clave: c for c in condiciones.por_eventos(lectura)}

    assert set(resultado) == {"evento:base:CRM", "evento:bloqueo", "evento:log:ERP"}
    assert (
        resultado["evento:base:CRM"].severidad == "critical" and resultado["evento:base:CRM"].push
    )
    bloqueo = resultado["evento:bloqueo"]
    assert (
        bloqueo.valor == 45.0 and "Sesión 87" in bloqueo.descripcion and "2." in bloqueo.descripcion
    )
    assert resultado["evento:log:ERP"].descripcion.startswith("El log de ERP está al 95%")


def _regla(**cambios) -> Regla:
    base = Regla(
        "r1", str(TENANT.id), "CPU alta", "cpu", ">", 50, "warning", None, 0, ("DBA",), True
    )
    return replace(base, **cambios)


def test_condiciones_por_reglas_y_operadores():
    reglas = [
        _regla(),
        _regla(id="r2", operador="<", valor=5),
        _regla(id="r3", conexion_id="otra"),
        _regla(id="r4", activa=False),
        _regla(id="r5", metrica="deadlocks"),
    ]
    resultado = condiciones.por_reglas(_lectura(cpu=60.0), reglas, "c1")
    assert [c.clave for c in resultado] == ["regla:r1"]
    assert resultado[0].descripcion == "Condición: CPU > 50%. Valor actual: 60%."
    assert _regla(operador=">=").cumple(50) and _regla(operador="<=").cumple(50)
    assert not _regla().cumple(None)


def test_evaluar_con_instancia_caida_solo_informa_la_caida():
    cumplidas, completa = condiciones.evaluar(None, "srv,1433", {}, [_regla()], "c1")
    assert [c.clave for c in cumplidas] == ["evento:instancia"] and completa is False
    assert "srv,1433" in cumplidas[0].descripcion
    cumplidas, completa = condiciones.evaluar(_lectura(cpu=99.0), "srv", {}, [_regla()], "c1")
    assert {c.clave for c in cumplidas} == {"umbral:cpu", "regla:r1"} and completa


# --- Repositorio en memoria ---------------------------------------------------------


class RepositorioAlertasFalso:
    def __init__(self):
        self.reglas: dict[str, Regla] = {}
        self.alertas: dict[int, Alerta] = {}
        self.ids = itertools.count(1)
        self.escrituras = 0

    def instalar(self, monkeypatch):
        for nombre in (
            "listar_reglas",
            "obtener_regla",
            "existe_nombre",
            "guardar_regla",
            "eliminar_regla",
            "disparar",
            "normalizar",
            "claves_activas",
            "listar",
            "obtener",
            "atender",
            "contar_activas",
        ):
            monkeypatch.setattr(repositorios, nombre, getattr(self, nombre))
        monkeypatch.setattr(monitor_repo, "listar_umbrales_del_tenant", lambda base, t: {})
        return self

    def listar_reglas(self, base, tenant_id):
        return sorted(self.reglas.values(), key=lambda r: r.nombre)

    def obtener_regla(self, base, tenant_id, regla_id):
        return self.reglas.get(regla_id)

    def existe_nombre(self, base, tenant_id, nombre, excluir_id):
        return any(r.nombre == nombre and r.id != excluir_id for r in self.reglas.values())

    def guardar_regla(self, base, regla, nueva):
        regla_id = f"00000000-0000-0000-0000-{next(self.ids):012d}" if nueva else regla.id
        self.reglas[regla_id] = replace(regla, id=regla_id)
        return regla_id

    def eliminar_regla(self, base, tenant_id, regla_id, usuario):
        self.reglas.pop(regla_id)
        cerradas = [
            a for a in self.alertas.values() if a.regla_id == regla_id and a.estado == "activa"
        ]
        for a in cerradas:
            self.alertas[a.id] = replace(a, estado="atendida", accion="regla_eliminada")
        return len(cerradas)

    def _activa(self, conexion_id, clave):
        return next(
            (
                a
                for a in self.alertas.values()
                if a.conexion_id == conexion_id and a.clave == clave and a.estado == "activa"
            ),
            None,
        )

    def disparar(self, base, tenant_id, conexion_id, datos, c: Condicion, solo_existente=False):
        self.escrituras += 1
        actual = self._activa(conexion_id, c.clave)
        if actual is None and solo_existente:
            return None
        if actual is None:
            ahora = datetime(2026, 10, 20, 12, 0)
            alerta = Alerta(
                next(self.ids),
                tenant_id,
                conexion_id,
                datos[0],
                datos[1],
                datos[2],
                c.origen,
                c.clave,
                c.regla_id,
                c.titulo,
                c.descripcion,
                c.severidad,
                c.metrica,
                c.valor,
                c.umbral,
                c.unidad,
                "activa",
                False,
                1,
                ahora,
                ahora,
            )
            self.alertas[alerta.id] = alerta
            return ResultadoDisparo(alerta, True, False)
        orden = ("info", "warning", "critical")
        escalo = orden.index(c.severidad) > orden.index(actual.severidad)
        alerta = replace(
            actual,
            severidad=c.severidad if escalo else actual.severidad,
            valor=c.valor,
            ocurrencias=actual.ocurrencias + (1 if actual.normalizada else 0),
            normalizada=False,
        )
        self.alertas[alerta.id] = alerta
        return ResultadoDisparo(alerta, False, escalo)

    def normalizar(self, base, tenant_id, conexion_id, clave):
        actual = self._activa(conexion_id, clave)
        if actual:
            self.alertas[actual.id] = replace(actual, normalizada=True)

    def claves_activas(self, base, tenant_id):
        return [
            (a.conexion_id, a.clave, a.severidad)
            for a in self.alertas.values()
            if a.estado == "activa" and not a.normalizada
        ]

    def listar(self, base, tenant_id, estado):
        return [a for a in self.alertas.values() if a.estado == estado]

    def obtener(self, base, tenant_id, alerta_id):
        return self.alertas.get(alerta_id)

    def atender(self, base, tenant_id, alerta_id, usuario, accion, comentario):
        alerta = self.alertas[alerta_id]
        if alerta.estado != "activa":
            return False
        self.alertas[alerta_id] = replace(
            alerta, estado="atendida", atendida_por=usuario, accion=accion, comentario=comentario
        )
        return True

    def contar_activas(self, base, tenant_id):
        resultado = {}
        for a in self.alertas.values():
            if a.estado == "activa":
                resultado[a.severidad] = resultado.get(a.severidad, 0) + 1
        return resultado


@pytest.fixture
def alertas(entorno, monkeypatch):  # noqa: F811
    repo = RepositorioAlertasFalso().instalar(monkeypatch)
    enviados = []

    async def enviar(tenant, roles, mensaje):
        enviados.append((roles, mensaje))
        return 1

    reloj = SimpleNamespace(ahora=1000.0)
    monkeypatch.setattr(motor.push, "enviar", enviar)
    monkeypatch.setattr(motor.time, "monotonic", lambda: reloj.ahora)
    return {**entorno, "alertas": repo, "enviados": enviados, "reloj": reloj}


async def _conexion():
    from app.modulos.conexiones import servicios as conexiones

    return await conexiones.crear(TENANT, _actor(), _datos())


# --- Motor --------------------------------------------------------------------------


async def test_motor_sostiene_escala_refresca_y_normaliza(alertas):
    conexion = await _conexion()
    m = motor.Motor()
    repo, reloj, enviados = alertas["alertas"], alertas["reloj"], alertas["enviados"]

    await m.evaluar(TENANT, conexion, _lectura(cpu=75.0))
    assert repo.alertas == {}
    reloj.ahora += 60
    await m.evaluar(TENANT, conexion, _lectura(cpu=75.0))
    [alerta] = repo.alertas.values()
    assert alerta.severidad == "warning" and enviados == []

    # Dentro del minuto y sin escalar no se escribe de nuevo.
    escrituras = repo.escrituras
    await m.evaluar(TENANT, conexion, _lectura(cpu=76.0))
    assert repo.escrituras == escrituras

    await m.evaluar(TENANT, conexion, _lectura(cpu=95.0))
    assert repo.alertas[alerta.id].severidad == "critical"
    roles, mensaje = enviados[-1]
    assert roles == ("DBA",) and mensaje["url"] == f"/alertas?id={alerta.id}"
    assert mensaje["titulo"] == "CRITICAL · ERP-PROD-01"

    reloj.ahora += 61
    await m.evaluar(TENANT, conexion, _lectura(cpu=96.0))
    assert repo.alertas[alerta.id].valor == 96.0

    await m.evaluar(TENANT, conexion, _lectura(cpu=10.0))
    assert repo.alertas[alerta.id].normalizada is True


async def test_motor_instancia_caida_y_recuperacion(alertas):
    conexion = await _conexion()
    m = motor.Motor()
    repo, reloj = alertas["alertas"], alertas["reloj"]

    await m.evaluar(TENANT, conexion, None)
    reloj.ahora += 5
    await m.evaluar(TENANT, conexion, None)
    [caida] = repo.alertas.values()
    assert caida.clave == "evento:instancia" and alertas["enviados"]

    await m.evaluar(TENANT, conexion, _lectura())
    assert repo.alertas[caida.id].normalizada is True


async def test_motor_retoma_alertas_al_reiniciar_y_respeta_las_atendidas(alertas):
    conexion = await _conexion()
    repo, reloj = alertas["alertas"], alertas["reloj"]
    primero = motor.Motor()
    reloj.ahora += 0
    await primero.evaluar(TENANT, conexion, None)
    reloj.ahora += 5
    await primero.evaluar(TENANT, conexion, None)
    [caida] = repo.alertas.values()

    # Un motor nuevo (reinicio del backend) normaliza la alerta que dejó en curso.
    segundo = motor.Motor()
    await segundo.evaluar(TENANT, conexion, _lectura())
    assert repo.alertas[caida.id].normalizada is True

    # Atendida con la condición vigente: no se recrea mientras persista.
    await segundo.evaluar(TENANT, conexion, None)
    reloj.ahora += 5
    await segundo.evaluar(TENANT, conexion, None)
    repo.atender("b", "t", caida.id, "dba@acme.com", "manual", "ok")
    reloj.ahora += 61
    await segundo.evaluar(TENANT, conexion, None)
    assert [a.estado for a in repo.alertas.values()] == ["atendida"]


async def test_motor_tolera_fallas_del_push(alertas, monkeypatch):
    conexion = await _conexion()

    async def fallar(*_):
        raise RuntimeError("sin red")

    monkeypatch.setattr(motor.push, "enviar", fallar)
    m = motor.Motor()
    await m.evaluar(TENANT, conexion, None)
    alertas["reloj"].ahora += 5
    await m.evaluar(TENANT, conexion, None)
    assert len(alertas["alertas"].alertas) == 1


# --- Servicios y API ----------------------------------------------------------------


async def test_reglas_validacion_y_permisos(alertas):
    conexion = await _conexion()
    dba = _actor()

    with pytest.raises(ErrorDominio) as error:
        await servicios.crear_regla(TENANT, _actor("CONSULTA"), _regla())
    assert error.value.codigo == "USR_PER_000"
    for cambios in (
        {"nombre": " "},
        {"metrica": "x"},
        {"destinatarios": ()},
        {"destinatarios": ("JEFE",)},
        {"sostenida_segundos": -1},
    ):
        with pytest.raises(ErrorDominio) as error:
            await servicios.crear_regla(TENANT, dba, _regla(**cambios))
        assert error.value.codigo == "USR_VAL_002"
    with pytest.raises(ErrorDominio) as error:
        await servicios.crear_regla(TENANT, dba, _regla(conexion_id=str(TENANT.id)))
    assert error.value.codigo == "CON_DB_002"

    regla = await servicios.crear_regla(
        TENANT, dba, _regla(conexion_id=conexion.id, destinatarios=("DBA", "DBA"))
    )
    assert regla.destinatarios == ("DBA",) and regla.creada_por == "dba@acme.com"
    with pytest.raises(ErrorDominio) as error:
        await servicios.crear_regla(TENANT, dba, _regla(nombre=" CPU alta "))
    assert error.value.codigo == "ALR_VAL_001"

    modificada = await servicios.modificar_regla(TENANT, dba, regla.id, _regla(valor=70))
    assert modificada.valor == 70
    assert [e[0] for e in alertas["eventos"][-2:]] == [
        "alerta.regla_creada",
        "alerta.regla_modificada",
    ]
    for regla_id in ("no-es-uuid", "00000000-0000-0000-0000-000000000999"):
        with pytest.raises(ErrorDominio) as error:
            await servicios.eliminar_regla(TENANT, dba, regla_id)
        assert error.value.codigo == "ALR_VAL_000"
    assert await servicios.eliminar_regla(TENANT, dba, regla.id) == 0


async def test_atender_valida_y_audita(alertas):
    conexion = await _conexion()
    repo = alertas["alertas"]
    resultado = repo.disparar(
        "b", str(TENANT.id), conexion.id, ("A", "S", "PROD"), condiciones.instancia_caida("S")
    )
    alerta_id = resultado.alerta.id

    for accion, comentario in (("otra", "x"), ("manual", "  "), ("manual", "x" * 1001)):
        with pytest.raises(ErrorDominio) as error:
            await servicios.atender(TENANT, _actor(), alerta_id, accion, comentario)
        assert error.value.codigo == "USR_VAL_002"
    with pytest.raises(ErrorDominio) as error:
        await servicios.atender(TENANT, _actor(), 999, "manual", "x")
    assert error.value.codigo == "ALR_VAL_000"

    atendida = await servicios.atender(
        TENANT, _actor(), alerta_id, "manual", "Reinicié el servicio"
    )
    assert atendida.estado == "atendida"
    accion, detalle = alertas["eventos"][-1]
    assert accion == "alerta.atendida" and detalle["comentario"] == "Reinicié el servicio"
    with pytest.raises(ErrorDominio) as error:
        await servicios.atender(TENANT, _actor(), alerta_id, "manual", "x")
    assert error.value.codigo == "ALR_VAL_002"
    with pytest.raises(ErrorDominio):
        await servicios.listar(TENANT, "otra")


async def test_api_alertas_por_rol(alertas):
    conexion = await _conexion()
    repo = alertas["alertas"]
    alerta = repo.disparar(
        "b", str(TENANT.id), conexion.id, ("A", "S", "PROD"), condiciones.instancia_caida("S")
    ).alerta
    consulta = await _navegador(alertas, "CONSULTA")

    listado = consulta.get("/api/alertas").json()
    assert listado["permisos"] == {"configurar": False, "atender": False}
    assert listado["alertas"][0]["disparada_en"].endswith("Z")
    assert consulta.get("/api/alertas/resumen").json() == {
        "activas": 1,
        "info": 0,
        "warning": 0,
        "critical": 1,
    }
    assert consulta.get(f"/api/alertas/{alerta.id}").json()["titulo"] == "Instancia sin conexión"
    denegado = consulta.post(
        f"/api/alertas/{alerta.id}/atender",
        json={"accion": "manual", "comentario": "x"},
        headers=CABECERA,
    )
    assert denegado.json()["codigo"] == "USR_PER_000"
    assert consulta.get("/alertas").status_code == 200

    dba = await _navegador(alertas, "DBA")
    cuerpo = {
        "nombre": "Sesiones altas",
        "metrica": "sesiones",
        "operador": ">",
        "valor": 300,
        "severidad": "warning",
        "destinatarios": ["DBA"],
    }
    creada = dba.post("/api/alertas/reglas", json=cuerpo, headers=CABECERA)
    assert creada.status_code == 201 and creada.json()["destinatarios"] == ["DBA"]
    regla_id = creada.json()["id"]
    assert dba.get("/api/alertas/reglas").json()["reglas"][0]["nombre"] == "Sesiones altas"
    assert (
        dba.put(
            f"/api/alertas/reglas/{regla_id}", json={**cuerpo, "valor": 400}, headers=CABECERA
        ).json()["valor"]
        == 400
    )
    assert dba.delete(f"/api/alertas/reglas/{regla_id}", headers=CABECERA).json() == {
        "alertas_cerradas": 0
    }
    atendida = dba.post(
        f"/api/alertas/{alerta.id}/atender",
        json={"accion": "falsa_alarma", "comentario": "ok"},
        headers=CABECERA,
    )
    assert atendida.json()["estado"] == "atendida"
    assert dba.get("/api/alertas?estado=atendida").json()["alertas"][0]["accion"] == "falsa_alarma"


# --- Push ---------------------------------------------------------------------------


def test_generar_claves_vapid_y_comando(capsys):
    publica, privada = push.generar_claves()
    assert len(publica) == 87 and len(privada) == 43
    assert cli.main(["generar-vapid"]) == 0
    assert "SECRETO_VAPID_CLAVE_PRIVADA=" in capsys.readouterr().out


@pytest.fixture
def push_configurado(monkeypatch):
    publica, privada = push.generar_claves()
    monkeypatch.setenv("VAPID_CLAVE_PUBLICA", publica)
    monkeypatch.setenv("VAPID_CONTACTO", "mailto:dba@acme.com")
    monkeypatch.setenv("SECRETO_VAPID_CLAVE_PRIVADA", privada)
    from app.core.config import obtener_configuracion

    obtener_configuracion.cache_clear()
    yield publica
    obtener_configuracion.cache_clear()


def test_envio_individual_informa_el_rechazo(push_configurado, monkeypatch):
    llamadas = []

    def webpush(**datos):
        llamadas.append(datos)
        if datos["subscription_info"]["endpoint"].endswith("vencida"):
            raise push.WebPushException("gone", response=SimpleNamespace(status_code=410))

    monkeypatch.setattr(push, "webpush", webpush)
    assert push._enviar_uno(push_repo.Suscripcion("https://p/ok", "k", "a"), "{}") is None
    assert push._enviar_uno(push_repo.Suscripcion("https://p/vencida", "k", "a"), "{}") == 410
    assert llamadas[0]["vapid_claims"] == {"sub": "mailto:dba@acme.com"}


async def test_enviar_sin_configuracion_no_hace_nada(monkeypatch):
    monkeypatch.delenv("SECRETO_VAPID_CLAVE_PRIVADA", raising=False)
    assert push.configurado() is False
    assert await push.enviar(TENANT, ("DBA",), {}) == 0


async def test_enviar_registra_fallas(push_configurado, monkeypatch):
    s = [push_repo.Suscripcion(f"https://p/{i}", "k", "a") for i in range(3)]
    monkeypatch.setattr(push_repo, "listar_por_roles", lambda base, t, roles: s)
    respuestas = iter([None, 500])

    def enviar(suscripcion, datos):
        if suscripcion.endpoint.endswith("2"):
            raise OSError("sin red")
        return next(respuestas)

    monkeypatch.setattr(push, "_enviar_uno", enviar)
    assert await push.enviar(TENANT, ("DBA",), {"titulo": "x"}) == 1


async def test_api_push(alertas, push_configurado, monkeypatch):
    guardadas, bajas = [], []
    monkeypatch.setattr(
        push_repo, "guardar", lambda base, t, u, s, agente: guardadas.append((s, agente))
    )
    monkeypatch.setattr(push_repo, "eliminar", lambda base, t, endpoint: bajas.append(endpoint))
    navegador = await _navegador(alertas, "CONSULTA")

    assert navegador.get("/api/push").json() == {
        "habilitado": True,
        "clave_publica": push_configurado,
    }
    suscripcion = {"endpoint": "https://push.example/x", "keys": {"p256dh": "k", "auth": "a"}}
    assert (
        navegador.post("/api/push/suscripciones", json=suscripcion, headers=CABECERA).status_code
        == 204
    )
    invalida = {**suscripcion, "endpoint": "http://inseguro"}
    assert (
        navegador.post("/api/push/suscripciones", json=invalida, headers=CABECERA).json()["codigo"]
        == "USR_VAL_002"
    )
    baja = navegador.post(
        "/api/push/suscripciones/baja",
        json={"endpoint": "https://push.example/x"},
        headers=CABECERA,
    )
    assert baja.status_code == 204
    assert guardadas[0][0].endpoint == "https://push.example/x" and bajas == [
        "https://push.example/x"
    ]
