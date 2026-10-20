import os

import pytest
from cryptography.fernet import Fernet
from pymongo import MongoClient

from app.core import cifrado
from app.core.errores import ErrorDominio
from app.core.secretos import proveedor_secretos
from app.identidad import repositorios as usuarios
from app.identidad.entidades import Sesion
from app.modulos.alertas import motor as modulo_motor
from app.modulos.alertas import servicios
from app.modulos.alertas.entidades import Regla
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import DatosConexion
from app.modulos.monitor import servicios as monitor
from app.modulos.monitor.entidades import Lectura, Umbral
from app.modulos.web_push import repositorios as suscripciones
from app.modulos.web_push import servicios as push
from app.tenants import servicios as tenants

pytestmark = pytest.mark.integracion


class Reloj:
    def __init__(self):
        self.ahora = 1000.0

    def __call__(self):
        return self.ahora


@pytest.fixture
def entorno(monkeypatch):
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    reloj = Reloj()
    monkeypatch.setattr(modulo_motor.time, "monotonic", reloj)
    enviados = []

    async def enviar(tenant, roles, mensaje):
        enviados.append((roles, mensaje["cuerpo"]))
        return 1

    monkeypatch.setattr(modulo_motor.notificaciones.push, "enviar", enviar)
    yield reloj, enviados
    cifrado.reiniciar()


def _lectura(cpu: float, **extra) -> Lectura:
    return Lectura({"cpu": cpu, "memoria": 10.0, "disco": 10.0, "latencia": 0.0}, **extra)


async def test_motor_y_atencion_de_alertas(prefijo_aislado, entorno):
    reloj, enviados = entorno
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    admin = Sesion("s", "u", str(tenant.id), "acme", "admin@acme.com", "Admin", "ADMINISTRADOR", 0)
    dba = Sesion("s", "u", str(tenant.id), "acme", "dba@acme.com", "Dba", "DBA", 0)
    conexion = await conexiones.crear(
        tenant,
        admin,
        DatosConexion(
            "LOCAL", os.environ.get("SQLSERVER_HOST", "localhost"), 14330, None, "DEV", "sa", "x"
        ),
    )
    await monitor.guardar_umbrales(tenant, admin, conexion.id, [Umbral("cpu", True, 70, 85)])
    motor = modulo_motor.Motor()

    # Warning sostenido un minuto: dispara sin push.
    await motor.evaluar(tenant, conexion, _lectura(75))
    assert await servicios.listar(tenant, "activa") == []
    reloj.ahora += 61
    await motor.evaluar(tenant, conexion, _lectura(76))
    [alerta] = await servicios.listar(tenant, "activa")
    assert alerta.severidad == "warning" and alerta.clave == "umbral:cpu" and enviados == []

    # Escala a critical: misma alerta, con push.
    reloj.ahora += 3
    await motor.evaluar(tenant, conexion, _lectura(95))
    [alerta] = await servicios.listar(tenant, "activa")
    assert alerta.severidad == "critical" and alerta.umbral == 85 and len(enviados) == 1

    # Se normaliza pero sigue activa; vuelve y suma una ocurrencia.
    await motor.evaluar(tenant, conexion, _lectura(20))
    assert (await servicios.obtener(tenant, alerta.id)).normalizada is True
    await motor.evaluar(tenant, conexion, _lectura(90))
    reloj.ahora += 61
    await motor.evaluar(tenant, conexion, _lectura(90))
    alerta = await servicios.obtener(tenant, alerta.id)
    assert alerta.ocurrencias == 2 and alerta.normalizada is False

    # Instancia caída: alerta crítica con push tras la confirmación; no normaliza el resto.
    await motor.evaluar(tenant, conexion, None)
    reloj.ahora += 6
    await motor.evaluar(tenant, conexion, None)
    activas = {a.clave: a for a in await servicios.listar(tenant, "activa")}
    assert set(activas) == {"umbral:cpu", "evento:instancia"}
    assert activas["umbral:cpu"].normalizada is False
    assert enviados[-1] == (("ADMINISTRADOR", "DBA"), "Instancia sin conexión")

    # Atender: Administrador y DBA, con comentario; no se puede atender dos veces.
    consulta = Sesion("s", "u", str(tenant.id), "acme", "c@acme.com", "C", "CONSULTA", 0)
    with pytest.raises(ErrorDominio) as error:
        await servicios.atender(tenant, consulta, alerta.id, "manual", "x")
    assert error.value.codigo == "USR_PER_000"
    atendida = await servicios.atender(tenant, dba, alerta.id, "falsa_alarma", "Pico esperado")
    assert atendida.estado == "atendida" and atendida.atendida_por == "dba@acme.com"
    with pytest.raises(ErrorDominio) as error:
        await servicios.atender(tenant, dba, alerta.id, "manual", "otra vez")
    assert error.value.codigo == "ALR_VAL_002"

    # Atendida con la condición vigente: no se vuelve a crear hasta que se normalice.
    reloj.ahora += 61
    await motor.evaluar(tenant, conexion, _lectura(95))
    assert "umbral:cpu" not in {a.clave for a in await servicios.listar(tenant, "activa")}

    # Regla propia: nombre único, dispara, y al eliminarla cierra sus alertas.
    regla = await servicios.crear_regla(
        tenant,
        dba,
        Regla("", "", "Memoria baja", "memoria", "<", 50, "info", None, 0, ("DBA",), True),
    )
    with pytest.raises(ErrorDominio) as error:
        await servicios.crear_regla(tenant, dba, regla)
    assert error.value.codigo == "ALR_VAL_001"
    motor.invalidar_configuracion()
    await motor.evaluar(tenant, conexion, _lectura(95))
    assert "regla:" + regla.id in {a.clave for a in await servicios.listar(tenant, "activa")}
    assert await servicios.eliminar_regla(tenant, dba, regla.id) == 1
    assert await servicios.listar_reglas(tenant) == []
    assert (await servicios.resumen(tenant))["activas"] == 1

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()


async def test_suscripciones_push_por_rol(prefijo_aislado, monkeypatch):
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    base, tenant_id = tenant.base_sqlserver, str(tenant.id)
    admin = usuarios.obtener_por_email(base, tenant_id, "admin@acme.com")
    dba_id = usuarios.crear(base, tenant_id, "dba@acme.com", None, "DBA", ())
    s_admin = suscripciones.Suscripcion("https://push.example/a", "k1", "a1")
    s_dba = suscripciones.Suscripcion("https://push.example/b", "k2", "a2")
    suscripciones.guardar(base, tenant_id, admin.id, s_admin, "Edge")
    suscripciones.guardar(base, tenant_id, dba_id, s_dba, "Chrome")
    # Reasignar el mismo navegador a otro usuario reemplaza la suscripción.
    suscripciones.guardar(base, tenant_id, dba_id, s_dba, "Chrome")

    assert suscripciones.listar_por_roles(base, tenant_id, ("DBA",)) == [s_dba]
    assert suscripciones.contar_del_usuario(base, tenant_id, dba_id) == 1
    assert usuarios.emails_por_roles(base, tenant_id, ("DBA", "ADMINISTRADOR")) == [
        "admin@acme.com",
        "dba@acme.com",
    ]
    assert usuarios.emails_por_roles(base, tenant_id, ()) == []

    monkeypatch.setattr(push, "configurado", lambda: True)
    respuestas = {"https://push.example/a": None, "https://push.example/b": 410}
    monkeypatch.setattr(push, "_enviar_uno", lambda s, datos: respuestas[s.endpoint])
    assert await push.enviar(tenant, ("ADMINISTRADOR", "DBA"), {"titulo": "x"}) == 1
    # La suscripción vencida (410) se elimina.
    assert suscripciones.listar_por_roles(base, tenant_id, ("DBA",)) == []

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()


async def test_desestimar_y_reinicio_del_motor(prefijo_aislado, entorno):
    reloj, enviados = entorno
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    dba = Sesion("s", "u", str(tenant.id), "acme", "dba@acme.com", "Dba", "DBA", 0)
    conexion = await conexiones.crear(
        tenant,
        dba,
        DatosConexion(
            "LOCAL", os.environ.get("SQLSERVER_HOST", "localhost"), 14330, None, "DEV", "sa", "x"
        ),
    )
    offline = _lectura(10, bases_no_disponibles=[{"base": "HIST", "estado": "OFFLINE"}])
    bloqueada = _lectura(
        10, bloqueos=[{"sid": 1, "bloqueador": 2, "base": "B", "recurso": "R", "espera_s": 40.0}]
    )

    motor = modulo_motor.Motor()
    for lectura in (offline, bloqueada):
        await motor.evaluar(tenant, conexion, lectura)
        reloj.ahora += 6
        await motor.evaluar(tenant, conexion, lectura)
    activas = {a.clave: a for a in await servicios.listar(tenant, "activa")}
    base_offline, bloqueo = activas["evento:base:HIST"], activas["evento:bloqueo"]

    # Desestimar la base offline; atender el bloqueo con la condición todavía vigente.
    await servicios.desestimar(tenant, dba, base_offline.id, "Base archivada", None)
    await servicios.atender(tenant, dba, bloqueo.id, "manual", "Se avisó al equipo")

    # Tras un reinicio, ninguna de las dos vuelve a crearse mientras persistan.
    reiniciado = modulo_motor.Motor()
    ambas = _lectura(
        10,
        bases_no_disponibles=[{"base": "HIST", "estado": "OFFLINE"}],
        bloqueos=bloqueada.bloqueos,
    )
    for _ in range(3):
        reloj.ahora += 61
        await reiniciado.evaluar(tenant, conexion, ambas)
    assert await servicios.listar(tenant, "activa") == []

    # El bloqueo se resuelve y vuelve: es una alerta nueva.
    await reiniciado.evaluar(tenant, conexion, offline)
    await reiniciado.evaluar(tenant, conexion, ambas)
    assert [a.clave for a in await servicios.listar(tenant, "activa")] == ["evento:bloqueo"]

    # Reactivar la desestimación: la base offline vuelve a alertar.
    [desestimada] = await servicios.listar_desestimadas(tenant)
    assert desestimada.motivo == "Base archivada" and desestimada.hasta is None
    await servicios.reactivar(tenant, dba, desestimada.id)
    reiniciado.invalidar_configuracion()
    await reiniciado.evaluar(tenant, conexion, ambas)
    reloj.ahora += 6
    await reiniciado.evaluar(tenant, conexion, ambas)
    claves = {a.clave for a in await servicios.listar(tenant, "activa")}
    assert claves == {"evento:bloqueo", "evento:base:HIST"}

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
