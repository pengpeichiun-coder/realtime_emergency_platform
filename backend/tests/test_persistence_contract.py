"""Fast storage-boundary tests; these do not claim to exercise PostgreSQL."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.schema import CreateIndex
from sqlalchemy.dialects import postgresql

from app.db.geo import coordinates, point
from app.db.models import Base, DispatchRecord
from app.main import create_app
from app.repositories.memory import MemoryRepository
from app.repositories.postgres import PostgresRepository
from app.schemas.operations import DispatchCreate


@pytest.mark.parametrize('lat,lon', [(1.283, 103.861), (-33.86, 151.21), (90, -180), (0, 0)])
def test_coordinates_roundtrip_and_order(lat, lon):
    from geoalchemy2.shape import to_shape
    value = point(lat, lon)
    assert coordinates(value) == (lat, lon)
    assert (to_shape(value).x, to_shape(value).y) == (lon, lat)


@pytest.mark.parametrize('lat,lon', [(91, 1), (1, 181), (float('nan'), 1), (1, float('inf'))])
def test_invalid_coordinates_rejected(lat, lon):
    with pytest.raises(ValueError):
        point(lat, lon)


def test_migration_offline_has_postgis_constraints_and_indexes():
    output = StringIO()
    config = Config('alembic.ini', output_buffer=output)
    config.attributes['database_url'] = 'postgresql+asyncpg://localhost/emergency_platform_test'
    command.upgrade(config, 'head', sql=True)
    sql = output.getvalue()
    for table in ['organizations', 'incidents', 'units', 'dispatches', 'activity_logs']:
        assert f'CREATE TABLE {table}' in sql
    for fragment in ['CREATE EXTENSION IF NOT EXISTS postgis', 'geography(POINT,4326)',
                     'USING gist', 'TIMESTAMP WITH TIME ZONE', 'JSONB',
                     'uq_dispatch_active_unit', 'uq_dispatch_active_incident', 'DEFERRABLE INITIALLY DEFERRED']:
        assert fragment in sql
    assert '0001_operational_schema' in sql


def test_schema_scopes_references_and_uniqueness():
    for name in ['incidents', 'units', 'dispatches', 'activity_logs']:
        assert set(Base.metadata.tables[name].primary_key.columns.keys()) == {'organization_id', 'id'}
    indexes = {index.name: str(CreateIndex(index).compile(dialect=postgresql.dialect()))
               for index in DispatchRecord.__table__.indexes}
    assert "WHERE status != 'completed'" in indexes['uq_dispatch_active_unit']
    for fk in DispatchRecord.__table__.foreign_key_constraints:
        if len(fk.columns) == 2:
            assert 'organization_id' in fk.columns


def test_default_application_uses_postgres_without_connecting_at_import():
    app = create_app()
    assert app.state.service is None
    assert app.state.tenants.database is None
    assert app.state.trusted_context is None


class CommitFailure(MemoryRepository):
    fail = False
    committed = False

    @asynccontextmanager
    async def transaction(self, *, write=False):
        async with super().transaction(write=write):
            yield
            if write and self.fail:
                raise RuntimeError('Injected commit failure')
        if write:
            self.committed = True


def test_commit_failure_rolls_back_without_events_or_task_start():
    async def scenario():
        repo = CommitFailure()
        app = create_app(repository=repo, dispatch_delay=10)
        service = app.state.service
        before = await service.snapshot()
        emitted = []
        service.connections.broadcast = emitted.append
        repo.fail = True
        with pytest.raises(RuntimeError, match='commit failure'):
            await service.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001'))
        assert await service.snapshot() == before
        assert not emitted and not service.simulator.tasks
        repo.fail = False
        def broadcast(event):
            assert repo.committed
            emitted.append(event)
        service.connections.broadcast = broadcast
        await service.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001'))
        assert emitted and service.simulator.tasks
        await service.shutdown()
    asyncio.run(scenario())


def test_failed_reset_resumes_rolled_back_dispatch():
    async def scenario():
        repo = CommitFailure()
        app = create_app(repository=repo, dispatch_delay=10)
        service = app.state.service
        dispatch = await service.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001'))
        repo.fail = True
        with pytest.raises(RuntimeError):
            await service.reset()
        assert dispatch.id in service.simulator.tasks
        assert (await service.get_dispatch(dispatch.id)).status == 'created'
        await service.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('status', ['created', 'en_route'])
def test_startup_resumes_interrupted_trips(status):
    async def scenario():
        repo = MemoryRepository()
        service = create_app(repository=repo, simulation_enabled=False).state.service
        dispatch = await service.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001'))
        if status == 'en_route':
            await service.start_dispatch(dispatch.id)
            repo.dispatches[dispatch.id].started_at = datetime.now(UTC) - timedelta(seconds=5)
        app = create_app(repository=repo, dispatch_delay=0, travel_duration=0.02, tick_interval=0.005)
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(asyncio.gather(*tuple(app.state.service.simulator.tasks.values())), 1)
            assert (await app.state.service.get_dispatch(dispatch.id)).status == 'arrived'
            unit = await app.state.service.get_unit('FIRE-001')
            incident = await app.state.service.get_incident('INC-001')
            assert (unit.latitude, unit.longitude) == (incident.latitude, incident.longitude)
        assert not app.state.service.simulator.tasks
    asyncio.run(scenario())


def test_nearby_endpoint_validation_and_no_fake_postgis(client):
    assert client.get('/api/incidents/INC-001/nearby-units?radius_meters=-1').status_code == 422
    assert client.get('/api/incidents/INC-001/nearby-units?limit=51').status_code == 422
    assert client.get('/api/incidents/missing/nearby-units').status_code == 404
    assert client.get('/api/incidents/INC-001/nearby-units').json()['error']['code'] == 'POSTGIS_REQUIRED'


@pytest.mark.parametrize('url', [
    'postgresql+asyncpg://localhost/emergency_platform',
    'postgresql+asyncpg://remote.example/emergency_platform_test',
    'sqlite:///emergency_platform_test',
    'postgresql+asyncpg://localhost/emergency_platform_test?database=development',
])
def test_database_test_target_safeguard(url):
    from database_support import safe_test_url
    with pytest.raises(ValueError):
        safe_test_url(url)


def test_database_test_target_rejects_development_alias(monkeypatch):
    from database_support import safe_test_url
    monkeypatch.setenv('DATABASE_URL', 'postgresql+asyncpg://user@127.0.0.1/emergency_platform_test')
    with pytest.raises(ValueError, match='development'):
        safe_test_url('postgresql+asyncpg://different_user@localhost:5432/emergency_platform_test')
