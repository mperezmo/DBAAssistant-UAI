"""Acceso de soporte de un operador a una empresa y errores de la aplicación saneados."""

import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from app.core.errores import ErrorDominio
from app.identidad import sesiones
from app.identidad.auth0 import ACR_MULTIFACTOR, CLAIM_MFA_EXIGIDO
from app.modulos.soporte import errores
from app.modulos.soporte import servicios as soporte_servicios
from app.plataforma import soporte
from app.tenants import resolucion
from app.tenants.entidades import Tenant
from tests.test_plataforma import (
    CABECERA,
    ORGANIZACION_PLATAFORMA,
    _ingresar,
    _navegador,
    _operador,
    _sesion,
    auth0,  # noqa: F401 (fixture)
    plataforma,  # noqa: F401 (fixture)
)

DEMO = Tenant(uuid4(), "demo", "Demo S.A.", "DB_T_demo", "db_t_demo", "activo", "org_demo")

TRAZA = """Traceback (most recent call last):
  File "/srv/backend/app/main.py", line 131, in registrar_contexto
    respuesta = await siguiente(request)
  File "/srv/.venv/lib/python3.14/site-packages/pyodbc.py", line 10, in execute
    raise Error(texto)
  File "/srv/backend/app/modulos/chat_ia/servicios.py", line 88, in ejecutar
    filas = cursor.execute("SELECT * FROM clientes WHERE email = 'ana@cliente.com'")
pyodbc.ProgrammingError: ('42S02', "Invalid object name 'Ventas.dbo.clientes'")
host sql01.cliente.local
"""


# --- Errores saneados ---------------------------------------------------------------


def test_errores_sin_datos_de_la_empresa():
    documento = {
        "ts": datetime(2026, 11, 24, 1, 2, 3),
        "nivel": "ERROR",
        "modulo": "app.http",
        "mensaje": "GET /api/chat/ventas 500 ana@cliente.com",
        "codigo": "TEN_APR_000",
        "traza": TRAZA,
        "usuario": "ana@cliente.com",
        "ruta": "/api/chat/hilos/123/Ventas",
        "ip": "10.0.0.5",
        "origen": "http",
        "http": {"metodo": "POST", "estado": 500, "plantilla": "/api/chat/hilos/{hilo_id}"},
    }

    saneado = errores.sanear(documento)

    assert saneado == {
        "ts": "2026-11-24T01:02:03Z",
        "nivel": "ERROR",
        "modulo": "app.http",
        "origen": "http",
        "metodo": "POST",
        "estado": 500,
        "ruta": "/api/chat/hilos/{hilo_id}",
        "codigo": "TEN_APR_000",
        "mensaje": "Falló el aprovisionamiento del tenant.",
        "excepcion": "pyodbc.ProgrammingError",
        "marcos": [
            "app/main.py:131 · registrar_contexto",
            "app/modulos/chat_ia/servicios.py:88 · ejecutar",
        ],
    }
    texto = json.dumps(saneado)
    for dato in ("ana@cliente.com", "10.0.0.5", "Ventas", "clientes", "sql01", "123"):
        assert dato not in texto


def test_tipo_de_excepcion_no_toma_lineas_del_mensaje():
    assert errores.tipo_de_excepcion(TRAZA) == "pyodbc.ProgrammingError"
    # Mensaje que empieza en la línea de la excepción pero no tiene la forma "Clase: ...".
    rara = (
        'Traceback (most recent call last):\n  File "/x/app/a.py", line 1, in f\n'
        "clientes de Ventas\n"
    )
    assert errores.tipo_de_excepcion(rara) is None
    assert errores.tipo_de_excepcion(None) is None
    # Errores anteriores a guardar la plantilla: la ruta no se reconstruye desde la URL.
    assert errores.sanear({"http": {"metodo": "GET", "estado": 500}})["ruta"] == "—"


# --- Pase de soporte ----------------------------------------------------------------


@pytest.fixture
def empresa(plataforma, monkeypatch):  # noqa: F811
    estado = {"tenant": DEMO, "avisos": []}
    monkeypatch.setattr(soporte.tenants, "obtener_por_id", lambda tid: estado["tenant"])

    async def obtener_tenant(slug):
        return estado["tenant"] if slug == "demo" else None

    monkeypatch.setattr(resolucion, "obtener_tenant", obtener_tenant)
    monkeypatch.setattr(
        soporte.usuarios,
        "emails_por_roles",
        lambda base, tid, roles: [("admin@demo.com", "en"), ("otra@demo.com", None)],
    )
    return estado


async def test_pase_exige_segundo_factor_y_sirve_una_sola_vez(plataforma, empresa):  # noqa: F811
    operador = _sesion(_operador(plataforma["repo"]))

    with pytest.raises(ErrorDominio) as error:
        await soporte.crear_pase(operador, str(DEMO.id))
    assert error.value.codigo == "AMB_VAL_000"

    confirmado = replace(operador, segundo_factor_en=time.time())
    url = await soporte.crear_pase(confirmado, str(DEMO.id))
    assert url.startswith("https://demo.localhost/auth/soporte?pase=")
    pase = parse_qs(urlparse(url).query)["pase"][0]

    otra = replace(DEMO, id=uuid4(), slug="otra")
    with pytest.raises(ErrorDominio):
        await soporte.canjear_pase(otra, pase)
    # El intento en otra empresa lo consume: no se puede probar en varias.
    with pytest.raises(ErrorDominio) as error:
        await soporte.canjear_pase(DEMO, pase)
    assert error.value.codigo == "USR_AUT_004"

    pase = parse_qs(urlparse(await soporte.crear_pase(confirmado, str(DEMO.id))).query)["pase"]
    sesion = await soporte.canjear_pase(DEMO, pase[0])
    assert (sesion.rol, sesion.tenant_id, sesion.email) == (
        "OPERADOR",
        str(DEMO.id),
        operador.email,
    )
    acciones = [accion for accion, _ in plataforma["eventos"]]
    assert "soporte.acceso_operador" in acciones and "plataforma.soporte_acceso" in acciones
    # Aviso a cada Administrador, en su idioma.
    avisos = {d: a for d, a, _ in plataforma["correos"]}
    assert avisos["admin@demo.com"] == "Support access to Demo S.A. on DBA Assistant"
    assert avisos["otra@demo.com"] == "Acceso de soporte a Demo S.A. en DBA Assistant"


async def test_no_se_entra_a_una_empresa_suspendida(plataforma, empresa):  # noqa: F811
    empresa["tenant"] = replace(DEMO, estado="suspendido")
    operador = replace(_sesion(_operador(plataforma["repo"])), segundo_factor_en=time.time())

    with pytest.raises(ErrorDominio) as error:
        await soporte.crear_pase(operador, str(DEMO.id))
    assert error.value.codigo == "TEN_VAL_003"


async def test_sesion_de_soporte_solo_ve_servicios_y_errores(plataforma, empresa, monkeypatch):  # noqa: F811
    async def servicios_falsos(tenant):
        return [{"nombre": "SQL Server", "estado": "online", "latencia_ms": 3, "detalle": "x"}]

    async def errores_falsos(tenant):
        return {"errores": [], "dias": 7, "maximo": 100}

    monkeypatch.setattr(soporte_servicios, "servicios", servicios_falsos)
    monkeypatch.setattr(
        soporte_servicios, "colas_y_procesos", lambda: {"colas": [], "procesos": []}
    )
    monkeypatch.setattr(errores, "listar", errores_falsos)
    repo = plataforma["repo"]
    operador = _operador(repo)
    confirmado = replace(_sesion(operador), segundo_factor_en=time.time())
    url = await soporte.crear_pase(confirmado, str(DEMO.id))

    empresa_web = _navegador("demo.localhost")
    entrada = empresa_web.get(urlparse(url).path + "?" + urlparse(url).query)
    assert entrada.status_code == 303 and entrada.headers["location"] == "/soporte"
    assert "max-age=3600" in entrada.headers["set-cookie"].lower()
    empresa_web.cookies.set(sesiones.COOKIE, entrada.cookies[sesiones.COOKIE])

    assert empresa_web.get("/").headers["location"] == "/soporte"
    pagina = empresa_web.get("/soporte")
    assert pagina.status_code == 200
    assert "soporteErrores" in pagina.text and "/api/soporte/bitacoras" not in pagina.text
    assert "Sesión de soporte." in pagina.text
    assert empresa_web.get("/api/soporte/servicios").status_code == 200
    assert empresa_web.get("/api/soporte/procesos").status_code == 200
    assert empresa_web.get("/api/soporte/errores").status_code == 200
    for prohibida in ("/api/soporte/bitacoras", "/api/soporte/datos", "/api/conexiones"):
        assert empresa_web.get(prohibida).json()["codigo"] == "USR_PER_000", prohibida
    assert empresa_web.get("/monitor").status_code == 403
    assert (
        empresa_web.put(
            "/api/soporte/bitacoras/excepciones/nivel", json={"nivel": "ERROR"}, headers=CABECERA
        ).status_code
        == 403
    )

    # Cerrar la sesión de soporte vuelve a la consola, sin salir de Auth0.
    salida = empresa_web.post("/auth/salir")
    assert salida.headers["location"] == "https://plataforma.localhost"
    assert "soporte.salida_operador" in [a for a, _ in plataforma["eventos"]]
    assert empresa_web.get("/api/soporte/servicios").status_code == 401


async def test_baja_del_operador_corta_la_sesion_de_soporte(plataforma, empresa):  # noqa: F811
    operador = _operador(plataforma["repo"])
    confirmado = replace(_sesion(operador), segundo_factor_en=time.time())
    pase = parse_qs(urlparse(await soporte.crear_pase(confirmado, str(DEMO.id))).query)["pase"]
    sesion = await soporte.canjear_pase(DEMO, pase[0])
    empresa_web = _navegador("demo.localhost")
    empresa_web.cookies.set(sesiones.COOKIE, sesion.id)

    plataforma["repo"].dar_de_baja_operador(operador.id)

    assert empresa_web.get("/api/soporte/errores").status_code == 401


# --- Confirmación con segundo factor en la consola ----------------------------------


async def test_confirmar_segundo_factor_antes_de_entrar(plataforma, auth0):  # noqa: F811
    repo = plataforma["repo"]
    operador = _operador(repo, "ops@plataforma.com")
    consola = _navegador("plataforma.localhost")
    respuesta, _ = _ingresar(consola, auth0, sub="auth0|ops@plataforma.com", email=operador.email)
    sesion_id = respuesta.cookies[sesiones.COOKIE]
    assert (
        consola.post(f"/api/plataforma/empresas/{DEMO.id}/soporte", headers=CABECERA).json()[
            "codigo"
        ]
        == "AMB_VAL_000"
    )

    pedido = consola.get("/auth/confirmar?volver=/%3Fsoporte%3Dabc")
    parametros = {k: v[0] for k, v in parse_qs(urlparse(pedido.headers["location"]).query).items()}
    assert parametros["acr_values"] == ACR_MULTIFACTOR
    assert parametros["organization"] == ORGANIZACION_PLATAFORMA

    auth0.identidad = {
        "nonce": parametros["nonce"],
        "org_id": ORGANIZACION_PLATAFORMA,
        "amr": ["mfa"],
        "sub": "auth0|ops@plataforma.com",
        CLAIM_MFA_EXIGIDO: True,
    }
    vuelta = consola.get(f"/auth/callback?code=abc&state={parametros['state']}")

    assert vuelta.status_code == 303 and vuelta.headers["location"] == "/?soporte=abc"
    assert (await sesiones.obtener(sesion_id)).segundo_factor_en is not None


async def test_confirmacion_sin_el_codigo_de_auth0_se_rechaza(plataforma, auth0):  # noqa: F811
    operador = _operador(plataforma["repo"], "ops@plataforma.com")
    consola = _navegador("plataforma.localhost")
    respuesta, _ = _ingresar(consola, auth0, sub="auth0|ops@plataforma.com", email=operador.email)
    sesion_id = respuesta.cookies[sesiones.COOKIE]
    pedido = consola.get("/auth/confirmar")
    parametros = {k: v[0] for k, v in parse_qs(urlparse(pedido.headers["location"]).query).items()}
    auth0.identidad = {
        "nonce": parametros["nonce"],
        "org_id": ORGANIZACION_PLATAFORMA,
        "amr": ["mfa"],
        "sub": "auth0|ops@plataforma.com",
    }

    vuelta = consola.get(f"/auth/callback?code=abc&state={parametros['state']}")

    assert vuelta.status_code == 401 and "USR_AUT_001" in vuelta.text
    assert (await sesiones.obtener(sesion_id)).segundo_factor_en is None


def test_fecha_utc():
    assert errores._fecha(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "2026-01-02T03:04:05Z"
