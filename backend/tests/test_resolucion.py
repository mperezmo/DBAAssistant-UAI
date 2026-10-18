from uuid import uuid4

import pytest

from app.tenants import resolucion
from app.tenants.entidades import Tenant


@pytest.mark.parametrize(
    "host,slug",
    [
        ("acme.localhost", "acme"),
        ("acme.localhost:443", "acme"),
        ("ACME.LOCALHOST", "acme"),
        ("mi-empresa.localhost", "mi-empresa"),
        ("localhost", None),
        ("a.b.localhost", None),
        ("acme.otrodominio.com", None),
        ("", None),
    ],
)
def test_slug_desde_host(host, slug):
    assert resolucion.slug_desde_host(host, "localhost") == slug


async def test_obtener_tenant_usa_cache(monkeypatch):
    resolucion.limpiar_cache()
    consultas = []
    tenant = Tenant(uuid4(), "acme", "ACME", "DB", "db", "activo")

    def obtener_por_slug(slug):
        consultas.append(slug)
        return tenant

    monkeypatch.setattr(resolucion.repositorios, "obtener_por_slug", obtener_por_slug)

    assert await resolucion.obtener_tenant("acme") is tenant
    assert await resolucion.obtener_tenant("acme") is tenant
    assert consultas == ["acme"]
    resolucion.limpiar_cache()
