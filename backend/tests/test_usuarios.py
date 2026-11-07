import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.identidad import invitaciones, perfil, sesiones, usuarios
from app.identidad.auth0 import ClienteAuth0
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


# --- Aviso de acceso por correo -----------------------------------------------------


@pytest.fixture
def smtp(monkeypatch):
    enviados = []
    monkeypatch.setattr(invitaciones.correo, "configurado", lambda: True)
    monkeypatch.setattr(
        invitaciones.correo,
        "enviar",
        lambda para, asunto, texto: enviados.append((para, asunto, texto)),
    )
    return enviados


async def test_alta_envia_el_aviso_y_se_puede_reenviar(entorno, smtp):
    navegador = await _navegador_con_sesion(entorno["admin"])

    creado = navegador.post(
        "/api/usuarios",
        json={"email": "ana@acme.com", "nombre": "Ana", "rol": "DBA", "ambientes": ["DEV"]},
        headers=CABECERA,
    ).json()
    assert creado["invitacion"] == "enviada"
    [(para, asunto, texto)] = smtp
    assert para == ["ana@acme.com"] and "ACME S.A." in asunto
    assert "https://acme.localhost" in texto and "DEV" in texto and "Admin" in texto
    assert entorno["eventos"][-1] == (
        "usuario.invitacion",
        {"email": "ana@acme.com", "estado": "enviada"},
    )

    reenvio = navegador.post(f"/api/usuarios/{creado['id']}/invitacion", headers=CABECERA)
    assert reenvio.status_code == 204 and len(smtp) == 2
    # Quien ya ingresó no necesita invitación.
    activo = entorno["repo"].agregar("ya@acme.com", "DBA", "activo")
    respuesta = navegador.post(f"/api/usuarios/{activo.id}/invitacion", headers=CABECERA)
    assert respuesta.json()["codigo"] == "USR_VAL_007"


async def test_aviso_sin_correo_o_con_falla(entorno, monkeypatch):
    admin = _sesion(entorno["admin"])
    usuario = await usuarios.crear(TENANT, admin, _datos())
    monkeypatch.setattr(invitaciones.correo, "configurado", lambda: False)
    # Al crear se informa; al reenviar es un error.
    assert await usuarios.invitar(TENANT, admin, usuario.id, al_crear=True) == "sin_correo"
    with pytest.raises(ErrorDominio) as error:
        await usuarios.invitar(TENANT, admin, usuario.id)
    assert error.value.codigo == "NTF_EML_001"

    def fallar(*argumentos):
        raise OSError("SMTP caído")

    monkeypatch.setattr(invitaciones.correo, "configurado", lambda: True)
    monkeypatch.setattr(invitaciones.correo, "enviar", fallar)
    assert await usuarios.invitar(TENANT, admin, usuario.id, al_crear=True) == "fallida"
    with pytest.raises(ErrorDominio) as error:
        await usuarios.invitar(TENANT, admin, usuario.id)
    assert error.value.codigo == "NTF_EML_000"


def test_aviso_en_el_idioma_del_usuario(entorno):
    usuario = entorno["repo"].agregar("bob@acme.com", "CONSULTA", "invitado", ambientes=())
    asunto, texto = invitaciones.armar(TENANT, usuario, None)
    assert asunto == "Tenés acceso a DBA Assistant · ACME S.A." and "ninguno por ahora" in texto
    ingles = replace(usuario, idioma="en")
    asunto, texto = invitaciones.armar(TENANT, ingles, "Ana")
    assert asunto.startswith("You have access") and "Ana gave you access" in texto


# --- Mi perfil ----------------------------------------------------------------------


@pytest.fixture
def dispositivos(monkeypatch):
    lista = {
        "d1": {
            "id": "d1",
            "agente": "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36",
            "creada_en": datetime(2026, 11, 1, 12, 0),
        }
    }
    monkeypatch.setattr(
        perfil.dispositivos, "listar_del_usuario", lambda base, t, u: list(lista.values())
    )
    # Mi perfil también lista las passkeys del usuario.
    monkeypatch.setattr(perfil.passkeys.repositorio, "listar", lambda base, t, u: [])
    monkeypatch.setattr(
        perfil.dispositivos,
        "eliminar_del_usuario",
        lambda base, t, u, d: lista.pop(d, None) is not None,
    )
    return lista


async def test_mi_perfil_datos_nombre_y_dispositivos(entorno, dispositivos):
    usuario = entorno["repo"].agregar(
        "lu@acme.com", "CONSULTA", "activo", "auth0|lu", "Lu", ambientes=("DEV",)
    )
    navegador = await _navegador_con_sesion(usuario)

    datos = navegador.get("/api/perfil").json()
    assert (datos["email"], datos["rol"], datos["ambientes"], datos["metodo"]) == (
        "lu@acme.com",
        "CONSULTA",
        ["DEV"],
        "contrasena",
    )
    assert datos["dispositivos"] == [
        {"id": "d1", "descripcion": "Chrome · Android", "creada_en": "2026-11-01T12:00:00Z"}
    ]

    assert (
        navegador.patch("/api/perfil", json={"nombre": " Luz "}, headers=CABECERA).status_code
        == 204
    )
    assert entorno["repo"].usuarios[usuario.id].nombre == "Luz"
    assert entorno["eventos"][-1] == ("usuario.perfil", {"nombre": "Luz"})

    assert navegador.delete("/api/perfil/dispositivos/d1", headers=CABECERA).status_code == 204
    assert dispositivos == {}
    assert navegador.delete("/api/perfil/dispositivos/d1", headers=CABECERA).status_code == 422
    # El rol Consulta también tiene su perfil, pero no el Panel Admin.
    assert navegador.get("/api/usuarios").json()["codigo"] == "USR_PER_000"


async def test_cambiar_contrasena_solo_con_cuenta_propia(entorno, monkeypatch):
    pedidos = []

    async def cambiar(self, email, organizacion):
        pedidos.append((email, organizacion))

    monkeypatch.setattr(perfil.ClienteAuth0, "cambiar_contrasena", cambiar)
    con_clave = entorno["repo"].agregar("lu@acme.com", "DBA", "activo", "auth0|lu")
    con_google = entorno["repo"].agregar("go@acme.com", "DBA", "activo", "google-oauth2|go")

    await perfil.cambiar_contrasena(TENANT, _sesion(con_clave))
    assert pedidos == [("lu@acme.com", "org_acme")]
    assert entorno["eventos"][-1] == ("usuario.cambio_contrasena", {})
    with pytest.raises(ErrorDominio) as error:
        await perfil.cambiar_contrasena(TENANT, _sesion(con_google))
    assert error.value.codigo == "USR_VAL_008"


def test_metodo_y_descripcion_del_dispositivo():
    assert perfil.metodo_de_ingreso("google-oauth2|1") == "google"
    assert perfil.metodo_de_ingreso("auth0|1") == "contrasena"
    assert perfil.metodo_de_ingreso("samlp|1") == "otro"
    assert perfil.metodo_de_ingreso(None) is None
    edge = "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36 Edg/130.0"
    assert perfil.describir_agente(edge) == "Edge · Windows"
    assert (
        perfil.describir_agente("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari/604.1")
        == "Safari · iOS"
    )
    assert perfil.describir_agente(None) == "—"


async def test_auth0_pide_el_correo_de_cambio_de_contrasena():
    recibidos = []

    def responder(request):
        recibidos.append((request.url.path, json.loads(request.content)))
        return httpx2.Response(200, text="We've just sent you an email to reset your password.")

    cliente = ClienteAuth0(
        SimpleNamespace(auth0_domain="dba-test.us.auth0.com", auth0_client_id="cliente-web"),
        transporte=httpx2.MockTransport(responder),
    )
    await cliente.cambiar_contrasena("lu@acme.com", "org_acme")
    assert recibidos == [
        (
            "/dbconnections/change_password",
            {
                "client_id": "cliente-web",
                "email": "lu@acme.com",
                "connection": "Username-Password-Authentication",
                "organization": "org_acme",
            },
        )
    ]

    def fallar(request):
        return httpx2.Response(429)

    cliente = ClienteAuth0(cliente.cfg, transporte=httpx2.MockTransport(fallar))
    with pytest.raises(ErrorDominio) as error:
        await cliente.cambiar_contrasena("lu@acme.com", None)
    assert error.value.codigo == "AUT_API_000"
