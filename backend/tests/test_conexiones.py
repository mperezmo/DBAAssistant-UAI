import re
import time
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core import cifrado
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.datos import winrm
from app.identidad import sesiones
from app.identidad.entidades import Sesion
from app.main import app
from app.modulos.conexiones import repositorios, servicios
from app.modulos.conexiones.entidades import DatosConexion, ResultadoPrueba, ServicioWindows
from app.tenants import resolucion
from app.tenants.entidades import Tenant
from tests.repositorio_falso import RepositorioUsuariosFalso

TENANT = Tenant(uuid4(), "acme", "ACME S.A.", "DB_T_acme", "db_t_acme", "activo", "org_acme")
CABECERA = {"X-Solicitud": "dba"}


class RepositorioConexionesFalso:
    def __init__(self):
        self.conexiones = {}

    def instalar(self, monkeypatch):
        for nombre in ("listar", "obtener", "existe_alias", "guardar", "eliminar"):
            monkeypatch.setattr(repositorios, nombre, getattr(self, nombre))
        return self

    def listar(self, base, tenant_id):
        return sorted(self.conexiones.values(), key=lambda c: c.alias)

    def obtener(self, base, tenant_id, conexion_id):
        return self.conexiones.get(conexion_id)

    def existe_alias(self, base, tenant_id, alias, excluir_id=None):
        return any(c.alias == alias and c.id != excluir_id for c in self.conexiones.values())

    def guardar(self, base, conexion, nueva):
        conexion_id = str(uuid4()) if nueva else conexion.id
        self.conexiones[conexion_id] = replace(conexion, id=conexion_id)
        return conexion_id

    def eliminar(self, base, tenant_id, conexion_id):
        self.conexiones.pop(conexion_id, None)


@pytest.fixture
def entorno(monkeypatch, redis_falso):
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    obtener_configuracion.cache_clear()
    repo = RepositorioConexionesFalso().instalar(monkeypatch)
    usuarios = RepositorioUsuariosFalso(str(TENANT.id)).instalar(monkeypatch)
    eventos, pruebas = [], []

    async def registrar(base, evento):
        eventos.append((evento.accion, evento.detalle))

    async def obtener_tenant(slug):
        return TENANT

    def probar(servidor, usuario, password):
        pruebas.append((servidor, usuario, password))
        if password == "mala":
            raise ErrorDominio("CON_DB_000", "Login failed for user")
        return ResultadoPrueba("16.0.4255.2", "Developer Edition", 3)

    monkeypatch.setattr(servicios.auditoria, "registrar", registrar)
    monkeypatch.setattr(servicios, "_probar", probar)
    monkeypatch.setattr(resolucion, "obtener_tenant", obtener_tenant)
    yield {"repo": repo, "usuarios": usuarios, "eventos": eventos, "pruebas": pruebas}
    cifrado.reiniciar()


def _datos(**cambios) -> DatosConexion:
    base = DatosConexion(
        alias="ERP-PROD-01",
        host="sql01.acme.local",
        puerto=None,
        instancia="ERP",
        ambiente="PROD",
        usuario_sql="dba_app",
        password_sql="S3creta!",
    )
    return replace(base, **cambios)


def _actor(rol="DBA", segundo_factor_en=None) -> Sesion:
    return Sesion(
        "s", "u", str(TENANT.id), "acme", "dba@acme.com", "Dba", rol, 0, segundo_factor_en
    )


# --- Cifrado ----------------------------------------------------------------------


def test_cifrado_ida_y_vuelta_y_deteccion_de_alteraciones(entorno):
    token = cifrado.cifrar("S3creta!")

    assert token != "S3creta!"
    assert cifrado.descifrar(token) == "S3creta!"
    with pytest.raises(ErrorDominio) as error:
        cifrado.descifrar(token[:-4] + "AAAA")
    assert error.value.codigo == "AKV_000"


def test_cifrado_sin_clave_configurada(monkeypatch):
    monkeypatch.delenv("SECRETO_CLAVE_CIFRADO", raising=False)
    cifrado.reiniciar()

    with pytest.raises(ErrorDominio) as error:
        cifrado.cifrar("x")
    assert error.value.codigo == "AKV_000"
    cifrado.reiniciar()


# --- Servicio ---------------------------------------------------------------------


async def test_crear_guarda_cifrado_y_audita_sin_contrasenas(entorno):
    conexion = await servicios.crear(TENANT, _actor(), _datos(winrm_usuario="ACME\\svc"))

    assert conexion.password_sql_cifrada != "S3creta!"
    assert cifrado.descifrar(conexion.password_sql_cifrada) == "S3creta!"
    assert conexion.winrm_puerto == 5985
    accion, detalle = entorno["eventos"][-1]
    assert accion == "conexion.creada"
    assert "S3creta!" not in str(detalle)


@pytest.mark.parametrize(
    "cambios,codigo",
    [
        ({"alias": ""}, "USR_VAL_002"),
        ({"password_sql": None}, "USR_VAL_002"),
        ({"host": "host con espacios"}, "USR_VAL_002"),
        ({"ambiente": "QA"}, "USR_VAL_002"),
        ({"puerto": 70000}, "USR_VAL_002"),
    ],
)
async def test_crear_valida_datos(entorno, cambios, codigo):
    with pytest.raises(ErrorDominio) as error:
        await servicios.crear(TENANT, _actor(), _datos(**cambios))
    assert error.value.codigo == codigo


async def test_alias_duplicado(entorno):
    await servicios.crear(TENANT, _actor(), _datos())

    with pytest.raises(ErrorDominio) as error:
        await servicios.crear(TENANT, _actor(), _datos())
    assert error.value.codigo == "CON_DB_001"


async def test_modificar_sin_contrasena_conserva_la_guardada(entorno):
    original = await servicios.crear(
        TENANT, _actor(), _datos(winrm_usuario="w", winrm_password="wp")
    )

    modificada = await servicios.modificar(
        TENANT,
        _actor(),
        original.id,
        _datos(alias="ERP-NUEVO", password_sql=None, winrm_usuario="w"),
    )

    assert modificada.alias == "ERP-NUEVO"
    assert cifrado.descifrar(modificada.password_sql_cifrada) == "S3creta!"
    assert cifrado.descifrar(modificada.winrm_password_cifrada) == "wp"


async def test_probar_arma_el_servidor_y_usa_la_contrasena_guardada(entorno):
    guardada = await servicios.crear(TENANT, _actor(), _datos())

    await servicios.probar(TENANT, _datos(puerto=1450), None)
    await servicios.probar(TENANT, _datos(password_sql=None), guardada.id)

    assert entorno["pruebas"] == [
        ("sql01.acme.local,1450", "dba_app", "S3creta!"),
        ("sql01.acme.local\\ERP", "dba_app", "S3creta!"),
    ]


async def test_probar_informa_la_falla(entorno):
    with pytest.raises(ErrorDominio) as error:
        await servicios.probar(TENANT, _datos(password_sql="mala"), None)
    assert error.value.codigo == "CON_DB_000"


async def test_eliminar_prod_exige_segundo_factor_reciente(entorno):
    conexion = await servicios.crear(TENANT, _actor(), _datos())

    with pytest.raises(ErrorDominio) as error:
        await servicios.eliminar(TENANT, _actor(segundo_factor_en=time.time() - 600), conexion.id)
    assert error.value.codigo == "AMB_VAL_000"

    await servicios.eliminar(TENANT, _actor(segundo_factor_en=time.time()), conexion.id)
    assert entorno["repo"].conexiones == {}
    assert entorno["eventos"][-1][0] == "conexion.eliminada"


async def test_eliminar_no_prod_no_exige_segundo_factor(entorno):
    conexion = await servicios.crear(TENANT, _actor(), _datos(ambiente="DEV"))

    await servicios.eliminar(TENANT, _actor(), conexion.id)

    assert entorno["repo"].conexiones == {}


async def test_conexion_inexistente(entorno):
    with pytest.raises(ErrorDominio) as error:
        await servicios.eliminar(TENANT, _actor(), "no-existe")
    assert error.value.codigo == "CON_DB_002"


# --- API y página -----------------------------------------------------------------


async def _navegador(entorno, rol, segundo_factor=True) -> TestClient:
    usuario = entorno["usuarios"].agregar(f"{rol.lower()}@acme.com", rol, "activo")
    sesion = await sesiones.crear(
        {
            "usuario_id": usuario.id,
            "tenant_id": str(TENANT.id),
            "tenant_slug": "acme",
            "email": usuario.email,
            "nombre": usuario.email,
            "rol": rol,
        },
        3600,
        tokens={},
    )
    if segundo_factor:
        await sesiones.registrar_segundo_factor(sesion.id)
    navegador = TestClient(app, base_url="https://acme.localhost", follow_redirects=False)
    navegador.cookies.set(sesiones.COOKIE, sesion.id)
    return navegador


ENTRADA = {
    "alias": "ERP-PROD-01",
    "host": "sql01.acme.local",
    "instancia": "ERP",
    "ambiente": "PROD",
    "usuario_sql": "dba_app",
    "password_sql": "S3creta!",
    "winrm_usuario": "ACME\\svc",
    "winrm_password": "Win!",
}


async def test_api_crud_sin_exponer_contrasenas(entorno):
    navegador = await _navegador(entorno, "DBA")

    creada = navegador.post("/api/conexiones", json=ENTRADA, headers=CABECERA)
    assert creada.status_code == 201
    cuerpo = creada.json()
    assert cuerpo["tiene_winrm"] is True and cuerpo["winrm_puerto"] == 5985
    assert "S3creta!" not in creada.text and "password" not in creada.text

    listado = navegador.get("/api/conexiones").json()["conexiones"]
    assert [c["alias"] for c in listado] == ["ERP-PROD-01"]

    cambios = {**ENTRADA, "alias": "ERP-PROD-02", "password_sql": None}
    modificada = navegador.patch(f"/api/conexiones/{cuerpo['id']}", json=cambios, headers=CABECERA)
    assert modificada.json()["alias"] == "ERP-PROD-02"

    prueba = navegador.post(
        "/api/conexiones/probar", json={**cambios, "id": cuerpo["id"]}, headers=CABECERA
    )
    assert prueba.json() == {"version": "16.0.4255.2", "edicion": "Developer Edition", "bases": 3}

    assert navegador.delete(f"/api/conexiones/{cuerpo['id']}", headers=CABECERA).status_code == 204


async def test_api_eliminar_prod_sin_segundo_factor(entorno):
    navegador = await _navegador(entorno, "ADMINISTRADOR", segundo_factor=False)
    creada = navegador.post("/api/conexiones", json=ENTRADA, headers=CABECERA).json()

    respuesta = navegador.delete(f"/api/conexiones/{creada['id']}", headers=CABECERA)

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "AMB_VAL_000"


async def test_rol_consulta_no_gestiona_conexiones(entorno):
    navegador = await _navegador(entorno, "CONSULTA")

    assert navegador.get("/api/conexiones").json()["codigo"] == "USR_PER_000"
    assert navegador.get("/conexiones").status_code == 403


async def test_pagina_conexiones(entorno):
    navegador = await _navegador(entorno, "DBA")

    html = navegador.get("/conexiones").text

    assert "Bases de datos" in html and "conexiones.js" in html
    assert 'href="/conexiones"' in html and 'href="/admin"' not in html
    # Los estáticos llevan la versión en la URL para no quedar en la caché del navegador.
    assert re.search(r'src="/static/js/conexiones\.js\?v=[0-9a-f]{12}"', html)


# --- Prueba de WinRM ----------------------------------------------------------------


@pytest.fixture
def winrm_falso(monkeypatch):
    llamadas = []
    respuesta = {"codigo": 0, "salida": "", "error": ""}

    def ejecutar(host, puerto, https, usuario, password, script):
        llamadas.append((host, puerto, https, usuario, password))
        if password == "mala":
            raise ConnectionError("401 Unauthorized")
        return winrm.ResultadoComando(**respuesta)

    monkeypatch.setattr(winrm, "ejecutar_powershell", ejecutar)
    return {"llamadas": llamadas, "respuesta": respuesta}


async def test_probar_winrm_lista_servicios_y_usa_la_contrasena_guardada(entorno, winrm_falso):
    guardada = await servicios.crear(
        TENANT, _actor(), _datos(winrm_usuario="PC\\dba", winrm_password="Win!")
    )
    winrm_falso["respuesta"]["salida"] = (
        '{"Name":"MSSQLSERVER","DisplayName":"SQL Server (MSSQLSERVER)","Status":"Running"}'
    )

    resultado = await servicios.probar_winrm(
        TENANT, _datos(winrm_usuario="PC\\dba", winrm_password=None), guardada.id
    )

    assert resultado == [ServicioWindows("MSSQLSERVER", "SQL Server (MSSQLSERVER)", "Running")]
    assert winrm_falso["llamadas"] == [("sql01.acme.local", 5985, False, "PC\\dba", "Win!")]


async def test_probar_winrm_varios_servicios_o_ninguno(entorno, winrm_falso):
    datos = _datos(winrm_usuario="PC\\dba", winrm_password="Win!", winrm_https=True)
    winrm_falso["respuesta"]["salida"] = (
        '[{"Name":"MSSQL$A","DisplayName":"A","Status":"Running"},'
        '{"Name":"MSSQL$B","DisplayName":"B","Status":"Stopped"}]'
    )
    assert [s.estado for s in await servicios.probar_winrm(TENANT, datos, None)] == [
        "Running",
        "Stopped",
    ]
    assert winrm_falso["llamadas"][0][1:3] == (5986, True)

    winrm_falso["respuesta"]["salida"] = ""
    assert await servicios.probar_winrm(TENANT, datos, None) == []


@pytest.mark.parametrize(
    ("cambios", "respuesta", "codigo"),
    [
        ({"winrm_usuario": None}, {}, "USR_VAL_002"),
        ({"winrm_password": "mala"}, {}, "WRM_000"),
        ({}, {"codigo": 1, "error": "Acceso denegado"}, "WRM_000"),
        ({}, {"salida": "no es json"}, "WRM_000"),
    ],
)
async def test_probar_winrm_fallas(entorno, winrm_falso, cambios, respuesta, codigo):
    winrm_falso["respuesta"].update(respuesta)
    datos = _datos(**{"winrm_usuario": "PC\\dba", "winrm_password": "Win!", **cambios})

    with pytest.raises(ErrorDominio) as error:
        await servicios.probar_winrm(TENANT, datos, None)
    assert error.value.codigo == codigo


async def test_api_probar_winrm(entorno, winrm_falso):
    navegador = await _navegador(entorno, "DBA")
    winrm_falso["respuesta"]["salida"] = (
        '{"Name":"MSSQLSERVER","DisplayName":"SQL Server","Status":"Stopped"}'
    )

    respuesta = navegador.post("/api/conexiones/probar-winrm", json=ENTRADA, headers=CABECERA)

    assert respuesta.json() == {
        "servicios": [
            {"nombre": "MSSQLSERVER", "nombre_visible": "SQL Server", "estado": "Stopped"}
        ]
    }


def test_ejecutar_powershell_arma_la_sesion(monkeypatch):
    creadas = []

    class SesionFalsa:
        def __init__(self, url, **opciones):
            creadas.append((url, opciones))

        def run_ps(self, script):
            return SimpleNamespace(status_code=0, std_out=b"[]", std_err=b"")

    monkeypatch.setattr(winrm.winrm, "Session", SesionFalsa)

    resultado = winrm.ejecutar_powershell("srv", 5986, True, "PC\\dba", "x", "Get-Service")

    assert resultado == winrm.ResultadoComando(0, "[]", "")
    url, opciones = creadas[0]
    assert url == "https://srv:5986/wsman"
    assert opciones["transport"] == "ntlm" and opciones["auth"] == ("PC\\dba", "x")
