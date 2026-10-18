from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import sesiones, usuarios
from app.identidad.entidades import Sesion
from app.main import app
from app.tenants import resolucion
from app.tenants.entidades import Tenant
from tests.repositorio_falso import RepositorioUsuariosFalso

TENANT = Tenant(uuid4(), "acme", "ACME S.A.", "DB_T_acme", "db_t_acme", "activo", "org_acme")


@pytest.fixture
def entorno(monkeypatch, redis_falso):
    monkeypatch.setenv("AUTH0_DOMAIN", "dba-test.us.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "cliente-web")
    obtener_configuracion.cache_clear()
    repo = RepositorioUsuariosFalso(str(TENANT.id)).instalar(monkeypatch)
    admin = repo.agregar("admin@acme.com", "ADMINISTRADOR", "activo", "google|admin", "Admin")
    eventos = []
    quitados = []

    async def registrar(base, evento):
        eventos.append((evento.accion, evento.detalle))

    async def obtener_tenant(slug):
        return TENANT if slug == "acme" else None

    async def quitar(self, organizacion, usuario):
        quitados.append((organizacion, usuario))

    monkeypatch.setattr(usuarios.auditoria, "registrar", registrar)
    monkeypatch.setattr(resolucion, "obtener_tenant", obtener_tenant)
    monkeypatch.setattr(usuarios.ClienteAuth0, "quitar_de_organizacion", quitar)
    yield {"repo": repo, "admin": admin, "eventos": eventos, "quitados": quitados}
    obtener_configuracion.cache_clear()


def _sesion(usuario) -> Sesion:
    return Sesion("s", usuario.id, str(TENANT.id), "acme", usuario.email, "X", usuario.rol, 0)


def _datos(email="nuevo@acme.com", rol="DBA", ambientes=("PROD", "DEV")):
    return usuarios.DatosUsuario(email, " Nuevo ", rol, tuple(ambientes))


# --- Reglas del servicio --------------------------------------------------------


async def test_crear_normaliza_y_queda_invitado(entorno):
    usuario = await usuarios.crear(TENANT, _sesion(entorno["admin"]), _datos(" NUEVO@acme.com "))

    assert (usuario.email, usuario.nombre, usuario.estado) == (
        "nuevo@acme.com",
        "Nuevo",
        "invitado",
    )
    assert usuario.ambientes == ("DEV", "PROD")
    assert entorno["eventos"][-1][0] == "usuario.creado"


@pytest.mark.parametrize(
    "datos,codigo",
    [
        (_datos(email="sin-arroba"), "USR_VAL_002"),
        (_datos(rol="SUPERUSUARIO"), "USR_PER_002"),
        (_datos(ambientes=("QA",)), "USR_VAL_002"),
        (_datos(email="admin@acme.com"), "USR_VAL_001"),
    ],
)
async def test_crear_rechaza_datos_invalidos(entorno, datos, codigo):
    with pytest.raises(ErrorDominio) as error:
        await usuarios.crear(TENANT, _sesion(entorno["admin"]), datos)
    assert error.value.codigo == codigo


async def test_email_dado_de_baja_sugiere_rehabilitar(entorno):
    entorno["repo"].agregar("ex@acme.com", estado="baja", fecha_baja=datetime.now(UTC))

    with pytest.raises(ErrorDominio) as error:
        await usuarios.crear(TENANT, _sesion(entorno["admin"]), _datos("ex@acme.com"))
    assert error.value.codigo == "USR_VAL_006"


async def test_modificar_registra_antes_y_despues(entorno):
    otro = entorno["repo"].agregar("dba@acme.com", "DBA", ambientes=("DEV",))

    actualizado = await usuarios.modificar(
        TENANT, _sesion(entorno["admin"]), otro.id, _datos(rol="CONSULTA", ambientes=("PROD",))
    )

    assert (actualizado.rol, actualizado.ambientes) == ("CONSULTA", ("PROD",))
    accion, detalle = entorno["eventos"][-1]
    assert accion == "usuario.modificado"
    assert detalle["antes"]["rol"] == "DBA" and detalle["despues"]["rol"] == "CONSULTA"


async def test_administrador_no_se_quita_el_rol_ni_se_da_de_baja(entorno):
    admin = entorno["admin"]
    entorno["repo"].agregar("otro-admin@acme.com", "ADMINISTRADOR")

    with pytest.raises(ErrorDominio) as error:
        await usuarios.modificar(TENANT, _sesion(admin), admin.id, _datos(rol="DBA"))
    assert error.value.codigo == "USR_PER_004"
    with pytest.raises(ErrorDominio) as error:
        await usuarios.dar_baja(TENANT, _sesion(admin), admin.id)
    assert error.value.codigo == "USR_PER_004"


async def test_siempre_queda_un_administrador(entorno):
    # El actor es un DBA con acceso hipotético: lo que se valida es la regla del último admin.
    actor = entorno["repo"].agregar("dba@acme.com", "DBA")

    with pytest.raises(ErrorDominio) as error:
        await usuarios.dar_baja(TENANT, _sesion(actor), entorno["admin"].id)
    assert error.value.codigo == "USR_PER_005"


async def test_baja_saca_de_la_organizacion_y_se_puede_rehabilitar(entorno):
    otro = entorno["repo"].agregar("dba@acme.com", "DBA", "activo", "google|dba")
    actor = _sesion(entorno["admin"])

    await usuarios.dar_baja(TENANT, actor, otro.id)

    assert entorno["quitados"] == [("org_acme", "google|dba")]
    assert [u.email for u in await usuarios.listar(TENANT, actor)] == ["admin@acme.com"]
    assert [u.email for u in await usuarios.listar(TENANT, actor, bajas=True)] == ["dba@acme.com"]

    rehabilitado = await usuarios.rehabilitar(TENANT, actor, otro.id)
    assert rehabilitado.estado == "activo"
    assert [e[0] for e in entorno["eventos"]][-2:] == ["usuario.baja", "usuario.rehabilitado"]


async def test_bajas_vencidas_se_purgan_al_listar(entorno):
    vieja = datetime.now(UTC) - timedelta(days=31)
    entorno["repo"].agregar("viejo@acme.com", estado="baja", fecha_baja=vieja)

    await usuarios.listar(TENANT, _sesion(entorno["admin"]), bajas=True)

    assert entorno["repo"].obtener_por_email(None, None, "viejo@acme.com") is None
    assert ("usuario.purgado", {"email": "viejo@acme.com"}) in entorno["eventos"]


async def test_usuario_inexistente(entorno):
    with pytest.raises(ErrorDominio) as error:
        await usuarios.rehabilitar(TENANT, _sesion(entorno["admin"]), "no-existe")
    assert error.value.codigo == "USR_VAL_000"


# --- API y página ---------------------------------------------------------------


async def _navegador_con_sesion(usuario) -> TestClient:
    sesion = await sesiones.crear(
        {
            "usuario_id": usuario.id,
            "tenant_id": str(TENANT.id),
            "tenant_slug": "acme",
            "email": usuario.email,
            "nombre": usuario.nombre or usuario.email,
            "rol": usuario.rol,
        },
        3600,
        tokens={},
    )
    navegador = TestClient(app, base_url="https://acme.localhost", follow_redirects=False)
    navegador.cookies.set(sesiones.COOKIE, sesion.id)
    return navegador


CABECERA = {"X-Solicitud": "dba"}


async def test_api_alta_modificacion_baja_y_rehabilitacion(entorno):
    navegador = await _navegador_con_sesion(entorno["admin"])

    creado = navegador.post(
        "/api/usuarios",
        json={"email": "nuevo@acme.com", "rol": "DBA", "ambientes": ["DEV"]},
        headers=CABECERA,
    )
    assert creado.status_code == 201
    usuario_id = creado.json()["id"]

    listado = navegador.get("/api/usuarios").json()
    assert listado["direccion_acceso"] == "https://acme.localhost"
    assert {u["email"] for u in listado["usuarios"]} == {"admin@acme.com", "nuevo@acme.com"}

    modificado = navegador.patch(
        f"/api/usuarios/{usuario_id}",
        json={"rol": "CONSULTA", "ambientes": ["PROD"]},
        headers=CABECERA,
    )
    assert modificado.json()["rol"] == "CONSULTA"

    assert navegador.delete(f"/api/usuarios/{usuario_id}", headers=CABECERA).status_code == 204
    bajas = navegador.get("/api/usuarios?bajas=true").json()["usuarios"]
    assert bajas[0]["dias_para_purga"] == 30

    rehabilitado = navegador.post(f"/api/usuarios/{usuario_id}/rehabilitar", headers=CABECERA)
    assert rehabilitado.json()["estado"] == "invitado"


async def test_api_errores_de_dominio_en_json(entorno):
    navegador = await _navegador_con_sesion(entorno["admin"])

    respuesta = navegador.post(
        "/api/usuarios", json={"email": "admin@acme.com", "rol": "DBA"}, headers=CABECERA
    )

    assert respuesta.status_code == 409
    assert respuesta.json()["codigo"] == "USR_VAL_001"


async def test_api_exige_cabecera_en_cambios(entorno):
    navegador = await _navegador_con_sesion(entorno["admin"])

    respuesta = navegador.post("/api/usuarios", json={"email": "x@acme.com", "rol": "DBA"})

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "USR_AUT_006"


async def test_api_sin_sesion_y_sin_permiso(entorno):
    sin_sesion = TestClient(app, base_url="https://acme.localhost")
    assert sin_sesion.get("/api/usuarios").json()["codigo"] == "USR_AUT_005"

    dba = entorno["repo"].agregar("dba@acme.com", "DBA")
    navegador = await _navegador_con_sesion(dba)
    respuesta = navegador.get("/api/usuarios")
    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "USR_PER_000"
    assert navegador.get("/admin").status_code == 403


async def test_pagina_admin(entorno):
    navegador = await _navegador_con_sesion(entorno["admin"])

    respuesta = navegador.get("/admin")

    assert respuesta.status_code == 200
    assert "Gestión de identidades" in respuesta.text
    assert "admin_usuarios.js" in respuesta.text
    assert "nav-item active" in respuesta.text


async def test_estado_de_servicios_para_el_panel(entorno, monkeypatch):
    from app.api import salud

    async def ok():
        return None

    async def caido():
        raise ConnectionError("sin respuesta")

    monkeypatch.setitem(salud.VERIFICACIONES, "sqlserver", ok)
    monkeypatch.setitem(salud.VERIFICACIONES, "mongodb", caido)
    monkeypatch.setitem(salud.VERIFICACIONES, "redis", ok)
    monkeypatch.setattr(salud.ClienteAuth0, "verificar", lambda self: ok())
    navegador = await _navegador_con_sesion(entorno["admin"])

    servicios = navegador.get("/api/salud/servicios").json()["servicios"]

    estados = {s["nombre"]: (s["estado"], s["detalle"]) for s in servicios}
    assert estados == {
        "SQL Server": ("online", "DB_T_acme"),
        "MongoDB": ("error", "db_t_acme"),
        "Redis": ("online", "Sesiones y caché"),
        "Auth0": ("online", "dba-test.us.auth0.com"),
    }
