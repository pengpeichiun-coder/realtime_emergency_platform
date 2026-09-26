import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.memory import MemoryRepository


@pytest.fixture
def app():
    return create_app(repository=MemoryRepository(), simulation_enabled=False)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
