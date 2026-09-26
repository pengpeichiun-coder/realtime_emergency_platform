from fastapi.testclient import TestClient
from app.main import create_app
from app.repositories.memory import MemoryRepository


def test_health_compatibility_and_dependency_readiness():
    with TestClient(create_app(repository=MemoryRepository(),simulation_enabled=False)) as client:
        assert client.get('/health').json()=={'status':'ok'}
        assert client.get('/health/ready').status_code==503
