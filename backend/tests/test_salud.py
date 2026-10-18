import pytest

from app.api import salud


async def _ok() -> None:
    return None


async def _falla() -> None:
    raise ConnectionError("sin respuesta")


@pytest.fixture
def dependencias(monkeypatch):
    def configurar(**estados):
        for nombre, funciona in estados.items():
            monkeypatch.setitem(salud.VERIFICACIONES, nombre, _ok if funciona else _falla)

    return configurar


def test_salud_ok_cuando_todas_las_dependencias_responden(cliente, dependencias):
    dependencias(sqlserver=True, mongodb=True, redis=True)

    respuesta = cliente.get("/api/salud")

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "estado": "ok",
        "componentes": {"sqlserver": "ok", "mongodb": "ok", "redis": "ok"},
    }


def test_salud_degradada_si_falla_una_dependencia(cliente, dependencias):
    dependencias(sqlserver=True, mongodb=False, redis=True)

    respuesta = cliente.get("/api/salud")

    assert respuesta.status_code == 503
    assert respuesta.json()["estado"] == "degradado"
    assert respuesta.json()["componentes"]["mongodb"] == "error"
