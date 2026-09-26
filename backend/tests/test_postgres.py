"""Real PostgreSQL/PostGIS integration tests, never SQLite or a geometry fake.

Set TEST_DATABASE_URL to an explicitly disposable local *_test database. These
checks migrate and reset application tables in that database only.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import os
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.geo import coordinates
from app.db.models import ActivityRecord, DispatchRecord, IncidentRecord, UnitRecord
from app.domain.transitions import DomainError
from app.main import create_app
from app.repositories.postgres import PostgresRepository
from app.schemas.operations import DispatchCreate, IncidentCreate
from app.scripts.seed_demo import seed
from app.services.connections import ConnectionManager
from app.services.operations import OperationsService

pytestmark = pytest.mark.postgres


from database_support import safe_test_url


@pytest.fixture(scope='module')
def pg_url():
    value = os.environ.get('TEST_DATABASE_URL')
    if not value:
        pytest.skip('TEST_DATABASE_URL not configured; real PostgreSQL/PostGIS integration is blocked')
    value = safe_test_url(value)
    config = Config('alembic.ini')
    config.attributes['database_url'] = value
    command.upgrade(config, 'head')
    yield value
    # Tables are intentionally retained for inspection; each case resets ORG-DEMO.


@asynccontextmanager
async def database_case(url):
    repository = PostgresRepository(url)
    await repository.open()
    try:
        await seed(repository)
        async with repository.transaction(write=True):
            await repository.reset()
        service = OperationsService(repository, ConnectionManager())
        yield repository, service
    finally:
        await repository.close()


def test_migration_and_spatial_indexes(pg_url):
    async def scenario():
        async with database_case(pg_url) as (repo, _):
            async with repo.transaction():
                assert await repo.session.scalar(text('SELECT version_num FROM alembic_version')) == '0008_queue_observability'
                assert await repo.session.scalar(text('SELECT PostGIS_Version()'))
                indexes = (await repo.session.execute(text("SELECT indexdef FROM pg_indexes WHERE tablename IN ('incidents', 'units')"))).scalars().all()
                assert sum('USING gist' in definition for definition in indexes) == 2
    asyncio.run(scenario())


def test_seed_is_explicit_and_idempotent(pg_url):
    async def scenario():
        async with database_case(pg_url) as (repo, service):
            before = await service.snapshot()
            assert len(before['incidents']) == 6 and len(before['units']) == 7
            assert await seed(repo) is False
            assert await service.snapshot() == before
    asyncio.run(scenario())


def test_incident_and_activity_survive_new_repository(pg_url):
    async def scenario():
        async with database_case(pg_url) as (_, service):
            incident = await service.create_incident(IncidentCreate(type='medical', severity='high',
                description='Durable call', latitude=-33.86, longitude=151.21))
        repo = PostgresRepository(pg_url)
        await repo.open()
        try:
            other = OperationsService(repo, ConnectionManager())
            assert await other.get_incident(incident.id) == incident
            assert any(item.entity_id == incident.id for item in (await other.snapshot())['activity'])
        finally:
            await repo.close()
    asyncio.run(scenario())


def test_dispatch_unit_and_arrival_persist(pg_url):
    async def scenario():
        async with database_case(pg_url) as (_, service):
            record = await service.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001'))
            await service.start_dispatch(record.id)
            await service.update_location(record.id, 1.291, 103.855)
            await service.arrive_dispatch(record.id)
        repo = PostgresRepository(pg_url)
        await repo.open()
        try:
            other = OperationsService(repo, ConnectionManager())
            assert (await other.get_dispatch(record.id)).status == 'arrived'
            unit, incident = await other.get_unit('FIRE-001'), await other.get_incident('INC-001')
            assert unit.status == incident.status == 'on_scene'
            assert (unit.latitude, unit.longitude) == (incident.latitude, incident.longitude)
            await other.resolve_incident(incident.id)
            assert (await other.get_dispatch(record.id)).completed_at is not None
        finally:
            await repo.close()
    asyncio.run(scenario())


def test_postgis_coordinate_roundtrip(pg_url):
    async def scenario():
        async with database_case(pg_url) as (repo, service):
            incident = await service.create_incident(IncidentCreate(type='traffic', severity='low',
                description='Coordinate order', latitude=-33.86, longitude=151.21))
            async with repo.transaction():
                row = await repo.session.scalar(select(IncidentRecord).where(IncidentRecord.id == incident.id))
                assert coordinates(row.location) == (-33.86, 151.21)
                xy = (await repo.session.execute(text('SELECT ST_X(location::geometry), ST_Y(location::geometry) FROM incidents WHERE id = :id'), {'id': incident.id})).one()
                assert tuple(xy) == (151.21, -33.86)
    asyncio.run(scenario())


def test_nearby_distance_order_radius_and_unavailable_exclusion(pg_url):
    async def scenario():
        async with database_case(pg_url) as (repo, service):
            async with repo.transaction(write=True):
                incident = await repo.incident('INC-001')
                template = await repo.unit('FIRE-001')
                for identifier, offset, status in [('NEAR', 0.001, 'available'), ('FAR', 0.01, 'available'), ('OFFLINE', 0, 'offline')]:
                    await repo.save_unit(replace(template, id=identifier, latitude=incident.latitude,
                        longitude=incident.longitude + offset, status=status, assigned_incident_id=None))
            results = await service.nearby_units('INC-001', 50000, 50)
            distances = [item['distance_meters'] for item in results]
            assert distances == sorted(distances)
            by_id = {item['unit_id']: item['distance_meters'] for item in results}
            assert 100 < by_id['NEAR'] < 120 and 1000 < by_id['FAR'] < 1200
            assert not {'OFFLINE', 'POL-002', 'AMB-002'} & by_id.keys()
            close = await service.nearby_units('INC-001', 200, 50)
            assert 'NEAR' in {item['unit_id'] for item in close}
            assert 'FAR' not in {item['unit_id'] for item in close}
            assert len(await service.nearby_units('INC-001', 50000, 1)) == 1
    asyncio.run(scenario())


def test_organization_scoping_and_reset_isolation(pg_url):
    async def scenario():
        async with database_case(pg_url) as (_, service):
            other = PostgresRepository(pg_url, organization_id='ORG-TEST-' + uuid4().hex[:8])
            await other.open()
            try:
                await seed(other)
                other_service = OperationsService(other, ConnectionManager())
                private = await other_service.create_incident(IncidentCreate(type='medical', severity='low',
                    description='Another organization', latitude=1.3, longitude=103.8))
                with pytest.raises(DomainError):
                    await service.get_incident(private.id)
                with pytest.raises(DomainError):
                    await service.nearby_units(private.id, 50000, 50)
                async with other.transaction(write=True):
                    unit = await other.unit('FIRE-001')
                    await other.save_unit(replace(unit, id='OTHER-ONLY'))
                assert 'OTHER-ONLY' not in {item['unit_id'] for item in await service.nearby_units('INC-001', 50000, 50)}
                await service.reset()
                assert (await other_service.get_incident(private.id)).description == 'Another organization'
            finally:
                # Clean this test-created organization without touching the demo.
                from sqlalchemy import delete
                from app.db.models import Organization
                async with other.transaction(write=True):
                    for model in [ActivityRecord, DispatchRecord, UnitRecord, IncidentRecord]:
                        await other.session.execute(delete(model).where(model.organization_id == other.organization_id))
                async with other.database.sessions.begin() as session:
                    await session.execute(delete(Organization).where(Organization.id == other.organization_id))
                await other.close()
    asyncio.run(scenario())


def test_dispatch_failure_rolls_back_all_writes_and_broadcasts(pg_url):
    async def scenario():
        async with database_case(pg_url) as (repo, service):
            before = await service.snapshot()
            emitted = []
            service.connections.broadcast = emitted.append
            original = repo.add_activity
            async def fail_after_activity(value):
                await original(value)
                raise RuntimeError('Injected storage failure')
            repo.add_activity = fail_after_activity
            with pytest.raises(RuntimeError):
                await service.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001'))
            assert await service.snapshot() == before
            assert not emitted
    asyncio.run(scenario())


def test_two_independent_services_cannot_double_dispatch(pg_url):
    async def scenario():
        async with database_case(pg_url) as (_, first):
            repo = PostgresRepository(pg_url)
            await repo.open()
            try:
                second = OperationsService(repo, ConnectionManager())
                results = await asyncio.gather(
                    first.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001')),
                    second.create_dispatch(DispatchCreate(incident_id='INC-002', unit_id='FIRE-001')),
                    return_exceptions=True)
                assert sum(isinstance(value, DomainError) and value.status_code == 409 for value in results) == 1
                active = [item for item in (await first.snapshot())['dispatches']
                          if item.unit_id == 'FIRE-001' and item.status != 'completed']
                assert len(active) == 1
            finally:
                await repo.close()
    asyncio.run(scenario())


def test_database_unique_index_prevents_bypassing_service(pg_url):
    async def scenario():
        async with database_case(pg_url) as (repo, service):
            record = await service.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001'))
            async with repo.database.sessions() as session:
                with pytest.raises(IntegrityError):
                    async with session.begin():
                        session.add(DispatchRecord(organization_id='ORG-DEMO', id='DSP-DUPLICATE',
                            incident_id='INC-002', unit_id='FIRE-001', status='created',
                            created_at=record.created_at, origin_latitude=1.3, origin_longitude=103.8))
            assert len((await service.snapshot())['dispatches']) == 2
    asyncio.run(scenario())


def test_demo_reset_is_persistent_and_transactional(pg_url):
    async def scenario():
        async with database_case(pg_url) as (_, service):
            await service.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001'))
            reset = await service.reset()
        repo = PostgresRepository(pg_url)
        await repo.open()
        try:
            state = await OperationsService(repo, ConnectionManager()).snapshot()
            for key in ['incidents', 'units', 'dispatches', 'activity']:
                assert state[key] == reset[key]
        finally:
            await repo.close()
    asyncio.run(scenario())


def test_simulation_persists_location_and_resumes_after_restart(pg_url):
    async def scenario():
        async with database_case(pg_url) as (_, service):
            record = await service.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001'))
            await service.start_dispatch(record.id)
            await service.update_location(record.id, 1.289, 103.851)
        # Verify persisted coordinates before a resumed simulator can update them.
        read_only = create_app(database_url=pg_url, simulation_enabled=False)
        async with read_only.router.lifespan_context(read_only):
            service = await read_only.state.tenants.get('ORG-DEMO')
            unit = await service.get_unit('FIRE-001')
            assert (unit.latitude, unit.longitude) == (1.289, 103.851)
        app = create_app(database_url=pg_url, dispatch_delay=0, travel_duration=0.3, tick_interval=0.01)
        async with app.router.lifespan_context(app):
            service = await app.state.tenants.get('ORG-DEMO')
            await asyncio.wait_for(asyncio.gather(*tuple(service.simulator.tasks.values())), 3)
            assert (await service.get_dispatch(record.id)).status == 'arrived'
        again = create_app(database_url=pg_url, simulation_enabled=False)
        async with again.router.lifespan_context(again):
            service = await again.state.tenants.get('ORG-DEMO')
            assert (await service.get_unit('FIRE-001')).status == 'on_scene'
            assert any(item.event_type == 'dispatch.updated' for item in (await service.snapshot())['activity'])
    asyncio.run(scenario())


def test_long_incident_area_can_be_dispatched(pg_url):
    async def scenario():
        async with database_case(pg_url) as (_, service):
            incident = await service.create_incident(IncidentCreate(type='medical', severity='high',
                description='Full-length area label', latitude=1.3, longitude=103.8, area='A' * 120))
            dispatch = await service.create_dispatch(DispatchCreate(incident_id=incident.id, unit_id='FIRE-001'))
            await service.start_dispatch(dispatch.id)
            assert (await service.get_unit('FIRE-001')).area == 'En route to ' + incident.area
            await service.arrive_dispatch(dispatch.id)
            assert (await service.get_unit('FIRE-001')).status == 'on_scene'
    asyncio.run(scenario())


def test_deferred_commit_failure_never_broadcasts(pg_url):
    async def scenario():
        from sqlalchemy import update
        async with database_case(pg_url) as (repo, service):
            before = await service.snapshot()
            emitted = []
            service.connections.broadcast = emitted.append
            original = repo.add_activity
            async def invalidate_deferred_reference(value):
                await original(value)
                await repo.session.execute(update(IncidentRecord).where(
                    IncidentRecord.organization_id == 'ORG-DEMO', IncidentRecord.id == 'INC-001'
                ).values(assigned_unit_id='MISSING-UNIT'))
            repo.add_activity = invalidate_deferred_reference
            with pytest.raises(DomainError) as error:
                await service.create_dispatch(DispatchCreate(incident_id='INC-001', unit_id='FIRE-001'))
            assert error.value.code == 'PERSISTENCE_CONFLICT'
            assert await service.snapshot() == before
            assert not emitted
    asyncio.run(scenario())


def test_realtime_cursor_is_shared_across_service_instances_and_restart(pg_url):
    async def scenario():
        async with database_case(pg_url) as (_, first):
            second_repository = PostgresRepository(pg_url)
            await second_repository.open()
            try:
                second = OperationsService(second_repository, ConnectionManager())
                before = await first.snapshot()
                await first.create_incident(IncidentCreate(type='medical', severity='high',
                    description='Shared cursor', latitude=1.29, longitude=103.85))
                after = await second.snapshot()
                assert after['stream_id'] == before['stream_id']
                assert after['sequence'] == before['sequence'] + 2
                await second.create_incident(IncidentCreate(type='medical', severity='low',
                    description='Other instance', latitude=1.29, longitude=103.85))
                assert (await first.snapshot())['sequence'] == after['sequence'] + 2
            finally:
                await second_repository.close()
    asyncio.run(scenario())
