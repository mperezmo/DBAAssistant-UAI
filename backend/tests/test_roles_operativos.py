"""El Administrador tiene las mismas facultades operativas que el DBA."""

from app.identidad.entidades import ROLES_OPERATIVOS, Sesion
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


def test_consulta_no_opera():
    assert not any(workarounds.permisos(_sesion("CONSULTA")).values())
    assert not any(buenas_practicas.permisos(_sesion("CONSULTA")).values())
    assert "CONSULTA" not in sandbox.ROLES_PROMUEVEN
