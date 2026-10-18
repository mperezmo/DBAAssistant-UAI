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
    monkeypatch.setattr(servicios.repositorios, "obtener_por_slug", lambda s: estado["existente"])
    monkeypatch.setattr(servicios.repositorios, "crear", lambda *a: _tenant())
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
