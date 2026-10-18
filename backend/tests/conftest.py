import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def cliente() -> TestClient:
    return TestClient(app)
