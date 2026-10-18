from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import obtener_configuracion
from app.identidad import servicios
from app.identidad.auth0 import ClienteAuth0
from app.identidad.entidades import Usuario
from app.main import app
from app.tenants import resolucion
from app.tenants.entidades import Tenant
from tests.auth0_falso import AUDIENCIA, CLIENT_ID, DOMINIO, EMISOR, ORGANIZACION, Auth0Falso

TENANT = Tenant(uuid4(), "acme", "ACME S.A.", "DB_T_acme", "db_t_acme", "activo", ORGANIZACION)


@pytest.fixture
def entorno(monkeypatch, redis_falso):
    monkeypatch.setenv("AUTH0_DOMAIN", DOMINIO)
    monkeypatch.setenv("AUTH0_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AUTH0_AUDIENCE", AUDIENCIA)
    monkeypatch.setenv("SECRETO_AUTH0_CLIENT_SECRET", "secreto")
    obtener_configuracion.cache_clear()

    auth0 = Auth0Falso()
    cliente_auth0 = ClienteAuth0(obtener_configuracion(), transporte=auth0.transporte())
    monkeypatch.setattr(servicios, "cliente_auth0", lambda: cliente_auth0)

    async def obtener_tenant(slug):
        return TENANT if slug == "acme" else None

    monkeypatch.setattr(resolucion, "obtener_tenant", obtener_tenant)

    usuarios = {
        "admin@acme.com": Usuario(
            str(uuid4()), str(TENANT.id), "admin@acme.com", None, "ADMINISTRADOR", "invitado", None
        ),
        "consulta@acme.com": Usuario(
            str(uuid4()),
            str(TENANT.id),
            "consulta@acme.com",
            "Ana",
            "CONSULTA",
            "activo",
            "google|ana",
        ),
        "baja@acme.com": Usuario(
            str(uuid4()), str(TENANT.id), "baja@acme.com", None, "DBA", "inactivo", "auth0|baja"
        ),
    }
    activados = []
    eventos = []

    def buscar(base, tenant_id, auth0_id, email):
        for usuario in usuarios.values():
            if usuario.auth0_usuario_id == auth0_id or (
                usuario.auth0_usuario_id is None and usuario.email == email
            ):
                return usuario
        return None

    async def registrar(base, evento):
        eventos.append(evento.accion)

    monkeypatch.setattr(servicios.repositorios, "buscar_para_ingreso", buscar)
    monkeypatch.setattr(servicios.repositorios, "activar", lambda *args: activados.append(args[2]))
    monkeypatch.setattr(servicios.auditoria, "registrar", registrar)

    yield {"auth0": auth0, "activados": activados, "eventos": eventos}
    obtener_configuracion.cache_clear()


def _navegador(host="acme.localhost") -> TestClient:
    return TestClient(app, base_url=f"https://{host}", follow_redirects=False)


def _ingresar(navegador, auth0, **identidad):
    respuesta = navegador.get("/auth/login")
    assert respuesta.status_code == 303
    url = urlparse(respuesta.headers["location"])
    parametros = {k: v[0] for k, v in parse_qs(url.query).items()}
    auth0.identidad = {
        "nonce": parametros["nonce"],
        "org_id": ORGANIZACION,
        "amr": ["mfa"],
        "email_verified": True,
        **identidad,
    }
    return navegador.get(f"/auth/callback?code=abc&state={parametros['state']}"), parametros


def test_dominio_base_pide_ingresar_por_la_direccion_de_la_empresa(entorno):
    respuesta = _navegador("localhost").get("/")

    assert respuesta.status_code == 200
    assert "dirección de tu empresa" in respuesta.text


def test_empresa_inexistente(entorno):
    respuesta = _navegador("otra.localhost").get("/")

    assert respuesta.status_code == 404
    assert "TEN_VAL_000" in respuesta.text


def test_sin_sesion_redirige_al_ingreso(entorno):
    navegador = _navegador()

    assert navegador.get("/").headers["location"] == "/ingresar"
    pagina = navegador.get("/ingresar")
    assert pagina.status_code == 200
    assert "ACME S.A." in pagina.text and "/auth/login" in pagina.text


def test_login_redirige_a_auth0_con_la_organizacion(entorno):
    _, parametros = _ingresar(_navegador(), entorno["auth0"], sub="google|ana")

    assert parametros["organization"] == ORGANIZACION
    assert parametros["client_id"] == CLIENT_ID
    assert parametros["audience"] == AUDIENCIA
    assert parametros["redirect_uri"] == "https://acme.localhost/auth/callback"


def test_primer_ingreso_activa_al_invitado_y_abre_sesion(entorno):
    navegador = _navegador()

    respuesta, _ = _ingresar(
        navegador, entorno["auth0"], sub="google|admin", email="admin@acme.com", name="Admin"
    )

    assert respuesta.status_code == 303 and respuesta.headers["location"] == "/"
    cookie = respuesta.headers["set-cookie"]
    assert "dba_sesion=" in cookie and "HttpOnly" in cookie and "Secure" in cookie
    assert "Max-Age=28800" in cookie
    assert entorno["activados"] == ["google|admin"]
    assert entorno["eventos"] == ["sesion.iniciada"]

    inicio = navegador.get("/")
    assert inicio.status_code == 200
    assert "Administrador" in inicio.text and 'href="/"' in inicio.text


def test_rol_consulta_no_ve_el_panel_admin(entorno):
    navegador = _navegador()
    _ingresar(navegador, entorno["auth0"], sub="google|ana", email="consulta@acme.com")

    html = navegador.get("/").text

    assert "Consulta" in html
    assert 'nav-item disabled" title="Disponible próximamente">' in html


@pytest.mark.parametrize(
    "identidad,codigo",
    [
        ({"sub": "google|x", "email": "nadie@acme.com"}, "USR_VAL_000"),
        ({"sub": "auth0|baja", "email": "baja@acme.com"}, "USR_VAL_005"),
        ({"sub": "auth0|admin", "email": "admin@acme.com", "email_verified": False}, "USR_AUT_003"),
        ({"sub": "google|ana", "amr": []}, "USR_AUT_001"),
        ({"sub": "google|ana", "org_id": "org_otra"}, "USR_AUT_000"),
    ],
)
def test_ingresos_rechazados(entorno, identidad, codigo):
    navegador = _navegador()
    respuesta = navegador.get("/auth/login")
    parametros = {
        k: v[0] for k, v in parse_qs(urlparse(respuesta.headers["location"]).query).items()
    }
    entorno["auth0"].identidad = {
        "nonce": parametros["nonce"],
        "org_id": ORGANIZACION,
        "amr": ["mfa"],
        "email_verified": True,
        **identidad,
    }

    respuesta = navegador.get(f"/auth/callback?code=abc&state={parametros['state']}")

    assert codigo in respuesta.text
    assert "set-cookie" not in respuesta.headers
    assert entorno["activados"] == []


def test_state_invalido_o_reutilizado(entorno):
    navegador = _navegador()
    _, parametros = _ingresar(navegador, entorno["auth0"], sub="google|ana")

    reutilizado = navegador.get(f"/auth/callback?code=abc&state={parametros['state']}")

    assert reutilizado.status_code == 400
    assert "USR_AUT_004" in reutilizado.text


def test_error_informado_por_auth0(entorno):
    respuesta = _navegador().get("/auth/callback?error=access_denied&error_description=MFA")

    assert respuesta.status_code == 401
    assert "USR_AUT_000" in respuesta.text


def test_token_con_firma_ajena_se_rechaza(entorno, monkeypatch):
    navegador = _navegador()
    otro = Auth0Falso()
    # Auth0 entrega tokens firmados con otra clave que la publicada en el JWKS.
    monkeypatch.setattr(entorno["auth0"], "firmar", otro.firmar)

    respuesta, _ = _ingresar(navegador, entorno["auth0"], sub="google|ana")

    assert "USR_AUT_000" in respuesta.text


def test_cerrar_sesion(entorno):
    navegador = _navegador()
    _ingresar(navegador, entorno["auth0"], sub="google|ana", email="consulta@acme.com")

    respuesta = navegador.post("/auth/salir")

    assert respuesta.headers["location"].startswith(f"{EMISOR}v2/logout")
    assert "returnTo=https%3A%2F%2Facme.localhost%2Fingresar" in respuesta.headers["location"]
    assert entorno["eventos"] == ["sesion.iniciada", "sesion.cerrada"]
    navegador.cookies.clear()
    assert navegador.get("/").headers["location"] == "/ingresar"


def test_tenant_sin_organizacion_no_permite_ingresar(entorno, monkeypatch):
    sin_org = Tenant(TENANT.id, "acme", "ACME", "DB", "db", "activo", None)

    async def obtener_tenant(slug):
        return sin_org

    monkeypatch.setattr(resolucion, "obtener_tenant", obtener_tenant)

    respuesta = _navegador().get("/auth/login")

    assert respuesta.status_code == 403
    assert "TEN_VAL_003" in respuesta.text


async def test_crear_organizacion_habilita_conexiones_con_alta_automatica(monkeypatch):
    monkeypatch.setenv("AUTH0_DOMAIN", DOMINIO)
    monkeypatch.setenv("AUTH0_ADMIN_CLIENT_ID", "cliente-admin")
    monkeypatch.setenv("SECRETO_AUTH0_ADMIN_CLIENT_SECRET", "secreto")
    obtener_configuracion.cache_clear()
    auth0 = Auth0Falso()

    organizacion = await ClienteAuth0(
        obtener_configuracion(), transporte=auth0.transporte()
    ).crear_organizacion("acme", "ACME S.A.")

    assert organizacion == ORGANIZACION
    creacion = next(c for c in auth0.llamadas if c[1] == "/api/v2/organizations")
    assert creacion[2] == {"name": "acme", "display_name": "ACME S.A."}
    habilitadas = [c[2] for c in auth0.llamadas if c[1].endswith("/enabled_connections")]
    assert habilitadas == [
        {"connection_id": "con_google-oauth2", "assign_membership_on_login": True},
        {
            "connection_id": "con_Username-Password-Authentication",
            "assign_membership_on_login": True,
        },
    ]
    obtener_configuracion.cache_clear()
