"""Facultades por rol: Administrador y DBA operan igual; Analista y Lectura, acotados."""

from dataclasses import replace

import pytest

from app.core.errores import ErrorDominio
from app.identidad import permisos
from app.identidad.entidades import (
    ACCIONES,
    AMBIENTES,
    MODIFICAR,
    PROMOVER,
    ROLES,
    ROLES_OPERATIVOS,
    Sesion,
    techo,
)
from app.modulos.alertas import condiciones
from app.modulos.buenas_practicas import servicios as buenas_practicas
from app.modulos.sandbox import servicios as sandbox
from app.modulos.workarounds import integrados
from app.modulos.workarounds import servicios as workarounds


def _sesion(rol: str) -> Sesion:
    return Sesion("s", "u", "t", "acme", "x@acme.com", "X", rol, 0)


def test_administrador_y_dba_operan_igual():
    for rol in ("ADMINISTRADOR", "DBA"):
        assert all(workarounds.permisos(_sesion(rol)).values())
        assert all(buenas_practicas.permisos(_sesion(rol)).values())
        assert rol in sandbox.ROLES_PROMUEVEN
        assert rol in condiciones.DESTINATARIOS_SISTEMA
    assert all(set(ROLES_OPERATIVOS) <= set(i.roles) for i in integrados.CATALOGO.values())


def test_lectura_no_opera():
    assert not any(workarounds.permisos(_sesion("LECTURA")).values())
    assert not any(buenas_practicas.permisos(_sesion("LECTURA")).values())
    assert "LECTURA" not in sandbox.ROLES_PROMUEVEN
    assert not any(_sesion("LECTURA").puede_modificar(a) for a in AMBIENTES)


def test_roles_de_una_empresa():
    assert ROLES == ("ADMINISTRADOR", "DBA", "ANALISTA", "LECTURA")
    for rol in ROLES_OPERATIVOS:
        assert all(techo(rol, a) == frozenset(ACCIONES) for a in AMBIENTES)
    assert not any(techo("LECTURA", a) for a in AMBIENTES)


def test_analista_modifica_fuera_de_prod_y_analiza():
    analista = _sesion("ANALISTA")
    assert [a for a in AMBIENTES if MODIFICAR in techo("ANALISTA", a)] == ["DEV", "TESTING"]
    assert techo("ANALISTA", "DEV") == {MODIFICAR, PROMOVER}
    assert not analista.puede_modificar("PROD")
    assert "ANALISTA" in sandbox.ROLES_PROMUEVEN
    assert buenas_practicas.permisos(analista) == {
        "analizar": True,
        "aplicar": False,
        "reglas": False,
    }
    assert not any(workarounds.permisos(analista).values())
    assert "ANALISTA" not in condiciones.DESTINATARIOS_SISTEMA
    for modulo in (
        "inicio",
        "chat",
        "monitor",
        "alertas",
        "sandbox",
        "contexto",
        "schema",
        "buenas_practicas",
    ):
        assert analista.puede_ver(modulo), modulo
    for modulo in ("workarounds", "auditoria", "conexiones", "admin", "soporte"):
        assert not analista.puede_ver(modulo), modulo


def test_lectura_conserva_los_modulos_de_consulta():
    lectura = _sesion("LECTURA")
    visibles = {
        m
        for m in ("inicio", "chat", "monitor", "alertas", "contexto", "schema")
        if lectura.puede_ver(m)
    }
    assert len(visibles) == 6
    assert not lectura.puede_ver("sandbox") and not lectura.puede_ver("buenas_practicas")


def test_permiso_efectivo_por_ambiente_y_restricciones():
    dba = replace(_sesion("DBA"), ambientes=("DEV", "PROD"), restricciones=("PROD:modificar",))
    assert dba.puede(MODIFICAR, "DEV")
    assert not dba.puede(MODIFICAR, "PROD")  # restringida por el Administrador
    assert dba.puede(PROMOVER, "PROD")
    assert not dba.puede(MODIFICAR, "TESTING")  # sin acceso al ambiente
    assert not dba.puede_en_todos(PROMOVER)
    with pytest.raises(ErrorDominio) as error:
        permisos.exigir(dba, MODIFICAR, "PROD")
    assert error.value.codigo == "USR_PER_006"
    with pytest.raises(ErrorDominio) as error:
        permisos.exigir(_sesion("LECTURA"), MODIFICAR, "DEV")
    assert error.value.codigo == "USR_PER_000"
    with pytest.raises(ErrorDominio) as error:
        permisos.exigir_en(dba, PROMOVER, None)  # alcanza a todos los ambientes
    assert error.value.codigo == "USR_PER_006"
    permisos.exigir_en(_sesion("DBA"), PROMOVER, None)
