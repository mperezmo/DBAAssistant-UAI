import pytest
from fakeredis import aioredis
from fastapi.testclient import TestClient

from app.datos import cache
from app.main import app


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(app)


@pytest.fixture
def redis_falso(monkeypatch):
    """Redis en memoria para las sesiones."""
    falso = aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache, "cliente", lambda: falso)
    return falso
