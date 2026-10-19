import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import obtener_configuracion
from app.identidad import reautenticacion, servicios, sesiones
from app.identidad.auth0 import ClienteAuth0
from app.main import app
from app.tenants import resolucion
from app.tenants.entidades import Tenant
from tests.auth0_falso import AUDIENCIA, CLIENT_ID, DOMINIO, ORGANIZACION, Auth0Falso
from tests.repositorio_falso import RepositorioUsuariosFalso

TENANT = Tenant(uuid4(), "acme", "ACME", "DB_T_acme", "db_t_acme", "activo", ORGANIZACION)


@pytest.fixture
def entorno(monkeypatch, redis_falso):
    monkeypatch.setenv("AUTH0_DOMAIN", DOMINIO)
    monkeypatch.setenv("AUTH0_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AUTH0_AUDIENCE", AUDIENCIA)
    monkeypatch.setenv("SECRETO_AUTH0_CLIENT_SECRET", "secreto")
    obtener_configuracion.cache_clear()
    auth0 = Auth0Falso()
    cliente = ClienteAuth0(obtener_configuracion(), transporte=auth0.transporte())
    monkeypatch.setattr(servicios, "cliente_auth0", lambda: cliente)

    async def obtener_tenant(slug):
        return TENANT

    async def registrar(base, evento):
        eventos.append(evento.accion)

    eventos = []
    monkeypatch.setattr(resolucion, "obtener_tenant", obtener_tenant)
    monkeypatch.setattr(servicios.auditoria, "registrar", registrar)
    repo = RepositorioUsuariosFalso(str(TENANT.id)).instalar(monkeypatch)
    usuario = repo.agregar("dba@acme.com", "DBA", "activo", "google|dba")
    yield {"auth0": auth0, "usuario": usuario, "eventos": eventos}
    obtener_configuracion.cache_clear()


async def _navegador_con_sesion(usuario):
    sesion = await sesiones.crear(
        {
            "usuario_id": usuario.id,
            "tenant_id": str(TENANT.id),
            "tenant_slug": "acme",
            "email": usuario.email,
            "nombre": "Dba",
            "rol": usuario.rol,
        },
        3600,
        tokens={},
    )
    navegador = TestClient(app, base_url="https://acme.localhost", follow_redirects=False)
    navegador.cookies.set(sesiones.COOKIE, sesion.id)
    return navegador, sesion


def _confirmar(navegador, auth0, **identidad):
    respuesta = navegador.get("/auth/confirmar?volver=/conexiones")
    parametros = {
        k: v[0] for k, v in parse_qs(urlparse(respuesta.headers["location"]).query).items()
    }
    auth0.identidad = {
        "nonce": parametros["nonce"],
        "org_id": ORGANIZACION,
        "amr": ["mfa"],
        "auth_time": int(time.time()),
        **identidad,
    }
    return parametros, navegador.get(f"/auth/callback?code=c&state={parametros['state']}")


async def test_confirmacion_fuerza_login_y_registra_el_segundo_factor(entorno):
    navegador, sesion = await _navegador_con_sesion(entorno["usuario"])
    assert (await sesiones.obtener(sesion.id)).segundo_factor_en is None

    parametros, respuesta = _confirmar(navegador, entorno["auth0"], sub="google|dba")

    assert parametros["prompt"] == "login" and parametros["max_age"] == "0"
    assert respuesta.headers["location"] == "/conexiones"
    assert "set-cookie" not in respuesta.headers  # no se abre una sesión nueva
    assert reautenticacion.segundo_factor_vigente(await sesiones.obtener(sesion.id))
    assert entorno["eventos"] == ["sesion.segundo_factor_confirmado"]


async def test_confirmacion_de_otra_persona_se_rechaza(entorno):
    navegador, _ = await _navegador_con_sesion(entorno["usuario"])

    _, respuesta = _confirmar(navegador, entorno["auth0"], sub="google|otro")

    assert "USR_AUT_000" in respuesta.text


async def test_confirmacion_sin_autenticacion_reciente_se_rechaza(entorno):
    navegador, _ = await _navegador_con_sesion(entorno["usuario"])

    _, respuesta = _confirmar(
        navegador, entorno["auth0"], sub="google|dba", auth_time=int(time.time()) - 3600
    )

    assert "USR_AUT_001" in respuesta.text


async def test_confirmar_sin_sesion_va_al_ingreso(entorno):
    navegador = TestClient(app, base_url="https://acme.localhost", follow_redirects=False)

    assert navegador.get("/auth/confirmar").headers["location"] == "/ingresar"


@pytest.mark.parametrize(
    "volver,esperado",
    [("/conexiones", "/conexiones"), ("//evil.com", "/"), ("https://evil.com", "/"), (None, "/")],
)
def test_ruta_de_retorno_solo_interna(volver, esperado):
    assert reautenticacion.ruta_de_retorno_valida(volver) == esperado
