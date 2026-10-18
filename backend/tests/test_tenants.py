from uuid import uuid4

import pytest

from app.core.config import Configuracion
from app.core.errores import ErrorDominio
from app.tenants import servicios
from app.tenants.entidades import Tenant, nombres_de_bases, validar_slug


@pytest.mark.parametrize("slug", ["acme", "a", "mi-empresa-2"])
def test_slug_valido(slug):
    assert validar_slug(slug) == slug


@pytest.mark.parametrize("slug", ["", "ACME", "-acme", "acme-", "a_b", "a;drop", "x" * 41])
def test_slug_invalido(slug):
    with pytest.raises(ErrorDominio) as error:
        validar_slug(slug)
    assert error.value.codigo == "TEN_VAL_002"


def test_nombres_de_bases_por_tenant():
    cfg = Configuracion(sqlserver_prefijo_base="DBAAssistant", mongodb_prefijo_base="dba")

    assert nombres_de_bases("mi-empresa", cfg) == ("DBAAssistant_T_mi_empresa", "dba_t_mi_empresa")


def _tenant(estado="aprovisionando") -> Tenant:
    return Tenant(uuid4(), "acme", "ACME", "DB_T_acme", "db_t_acme", estado)


@pytest.fixture
def entorno(monkeypatch):
    """Reemplaza el acceso a datos por dobles que registran las llamadas."""
    llamadas = []
    estado = {"existente": None}

    async def preparar_mongo(nombre):
        llamadas.append(("mongo", nombre))

    async def registrar_auditoria(base, evento):
        llamadas.append(("auditoria", evento.accion))

    monkeypatch.setattr(servicios, "migrar_catalogo", lambda: llamadas.append(("catalogo",)))

    def crear(*_):
        estado["existente"] = _tenant()
        return estado["existente"]

    monkeypatch.setattr(servicios.repositorios, "obtener_por_slug", lambda s: estado["existente"])
    monkeypatch.setattr(servicios.repositorios, "crear", crear)
    monkeypatch.setattr(
        servicios.repositorios,
        "actualizar_estado",
        lambda tid, est, detalle=None: llamadas.append(("estado", est)),
    )
    monkeypatch.setattr(
        servicios.repositorios, "crear_administrador", lambda t, e: llamadas.append(("admin", e))
    )
    monkeypatch.setattr(
        servicios.sqlserver, "crear_base_si_no_existe", lambda n: llamadas.append(("base", n))
    )
    monkeypatch.setattr(
        servicios.migraciones, "aplicar", lambda b, c: llamadas.append(("migrar", b, c))
    )
    monkeypatch.setattr(servicios.mongo, "preparar_base_tenant", preparar_mongo)
    monkeypatch.setattr(servicios.auditoria, "registrar", registrar_auditoria)
    return llamadas, estado


async def test_aprovisionar_ejecuta_todos_los_pasos(entorno):
    llamadas, _ = entorno

    tenant = await servicios.aprovisionar("acme", "ACME", "admin@acme.com")

    assert tenant.slug == "acme"
    assert llamadas == [
        ("catalogo",),
        ("base", "DB_T_acme"),
        ("migrar", "DB_T_acme", "tenant"),
        ("mongo", "db_t_acme"),
        ("admin", "admin@acme.com"),
        ("estado", "activo"),
        ("auditoria", "tenant.aprovisionado"),
    ]


async def test_aprovisionar_rechaza_slug_existente(entorno):
    _, estado = entorno
    estado["existente"] = _tenant("activo")

    with pytest.raises(ErrorDominio) as error:
        await servicios.aprovisionar("acme", "ACME", "admin@acme.com")
    assert error.value.codigo == "TEN_VAL_001"


async def test_aprovisionar_retoma_tenant_con_error(entorno):
    llamadas, estado = entorno
    estado["existente"] = _tenant("error")

    await servicios.aprovisionar("acme", "ACME", "admin@acme.com")

    assert ("estado", "activo") in llamadas


async def test_falla_de_un_paso_marca_el_tenant_con_error(entorno, monkeypatch):
    llamadas, _ = entorno

    async def mongo_caido(nombre):
        raise ConnectionError("sin MongoDB")

    monkeypatch.setattr(servicios.mongo, "preparar_base_tenant", mongo_caido)

    with pytest.raises(ErrorDominio) as error:
        await servicios.aprovisionar("acme", "ACME", "admin@acme.com")
    assert error.value.codigo == "TEN_APR_000"
    assert ("estado", "error") in llamadas
    assert ("estado", "activo") not in llamadas


@pytest.mark.parametrize("nombre,email", [("", "a@b.com"), ("ACME", "sin-arroba")])
async def test_aprovisionar_valida_datos(entorno, nombre, email):
    with pytest.raises(ErrorDominio) as error:
        await servicios.aprovisionar("acme", nombre, email)
    assert error.value.codigo == "USR_VAL_002"


@pytest.fixture
def con_auth0(monkeypatch, entorno):
    llamadas, _ = entorno
    monkeypatch.setenv("AUTH0_DOMAIN", "dba-test.us.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "cliente-web")
    servicios.obtener_configuracion.cache_clear()

    async def crear_organizacion(self, nombre, nombre_visible):
        llamadas.append(("auth0", nombre, nombre_visible))
        return "org_acme"

    async def registrar_direcciones(self, url_base):
        llamadas.append(("direcciones", url_base))

    monkeypatch.setattr(servicios.ClienteAuth0, "crear_organizacion", crear_organizacion)
    monkeypatch.setattr(servicios.ClienteAuth0, "registrar_direcciones", registrar_direcciones)
    monkeypatch.setattr(
        servicios.repositorios,
        "actualizar_organizacion",
        lambda tid, org: llamadas.append(("organizacion", org)),
    )
    yield llamadas
    servicios.obtener_configuracion.cache_clear()


async def test_aprovisionar_crea_la_organizacion_en_auth0(con_auth0):
    await servicios.aprovisionar("acme", "ACME S.A.", "admin@acme.com")

    assert ("direcciones", "https://acme.localhost") in con_auth0
    assert ("auth0", "acme", "ACME S.A.") in con_auth0
    assert ("organizacion", "org_acme") in con_auth0
    assert con_auth0.index(("organizacion", "org_acme")) < con_auth0.index(("estado", "activo"))


async def test_tenant_activo_sin_organizacion_se_completa(con_auth0, entorno):
    _, estado = entorno
    estado["existente"] = _tenant("activo")

    await servicios.aprovisionar("acme", "ACME S.A.", "admin@acme.com")

    assert ("organizacion", "org_acme") in con_auth0


async def test_sincronizar_auth0_registra_las_direcciones(con_auth0, entorno):
    _, estado = entorno
    estado["existente"] = _tenant("activo")

    assert await servicios.sincronizar_auth0("acme") == "https://acme.localhost"
    assert ("direcciones", "https://acme.localhost") in con_auth0


async def test_sincronizar_auth0_tenant_inexistente(con_auth0):
    with pytest.raises(ErrorDominio) as error:
        await servicios.sincronizar_auth0("nadie")
    assert error.value.codigo == "TEN_VAL_000"
