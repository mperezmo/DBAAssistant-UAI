import pytest

from app import cli
from app.core.errores import ErrorDominio


def test_error_de_dominio_expone_mensaje_y_estado():
    error = ErrorDominio("USR_VAL_002", "Falta el email.")

    assert error.definicion.estado_http == 422
    assert "USR_VAL_002" in str(error)


def test_codigo_no_catalogado_no_se_permite():
    with pytest.raises(ValueError):
        ErrorDominio("NO_EXISTE_000")


def test_cli_crear_tenant(monkeypatch, capsys):
    recibido = {}

    async def aprovisionar(slug, nombre, admin):
        recibido.update(slug=slug, nombre=nombre, admin=admin)
        from uuid import uuid4

        from app.tenants.entidades import Tenant

        return Tenant(uuid4(), slug, nombre, "DB_T_acme", "db_t_acme", "activo")

    monkeypatch.setattr(cli.servicios, "aprovisionar", aprovisionar)

    codigo = cli.main(["crear-tenant", "--slug", "acme", "--nombre", "ACME", "--admin", "a@b.com"])

    assert codigo == 0
    assert recibido == {"slug": "acme", "nombre": "ACME", "admin": "a@b.com"}
    assert "DB_T_acme" in capsys.readouterr().out


def test_cli_informa_errores_de_dominio(monkeypatch, capsys):
    async def aprovisionar(*_):
        raise ErrorDominio("TEN_VAL_001", "acme")

    monkeypatch.setattr(cli.servicios, "aprovisionar", aprovisionar)

    codigo = cli.main(["crear-tenant", "--slug", "acme", "--nombre", "ACME", "--admin", "a@b.com"])

    assert codigo == 1
    assert "TEN_VAL_001" in capsys.readouterr().err


def test_cli_migrar(monkeypatch, capsys):
    monkeypatch.setattr(cli.servicios, "migrar_todo", lambda: None)

    assert cli.main(["migrar"]) == 0
    assert "actualizada" in capsys.readouterr().out
