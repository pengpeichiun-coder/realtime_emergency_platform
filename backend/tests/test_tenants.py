"""API and socket boundaries against two real PostgreSQL organizations."""
import asyncio
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from app.context import RequestContext
from app.main import create_app
from app.repositories.postgres import PostgresRepository
from app.schemas.operations import IncidentCreate, DispatchCreate
from app.scripts.seed_demo import seed
from app.services.connections import ConnectionManager
from app.services.operations import OperationsService
from test_postgres import pg_url

pytestmark = pytest.mark.postgres

@pytest.fixture
def tenant_app(pg_url):
    async def prepare():
        private = {}
        for organization in ['ORG-A', 'ORG-B']:
            repo = PostgresRepository(pg_url, organization_id=organization)
            await repo.open()
            await seed(repo)
            service = OperationsService(repo, ConnectionManager())
            await service.reset()
            incident = await service.create_incident(IncidentCreate(type='medical', severity='high',
                title=organization, description='Tenant boundary fixture', latitude=1.3, longitude=103.85))
            async with repo.transaction(write=True):
                unit = await repo.unit('FIRE-001')
                await repo.save_unit(replace(unit, id=organization + '-UNIT'))
            private[organization] = incident.id
            await repo.close()
        return private
    private = asyncio.run(prepare())
    app = create_app(database_url=pg_url, simulation_enabled=False,
        trusted_context=RequestContext('user-a', 'ORG-A', 'ADMIN'))
    with TestClient(app) as client:
        yield app, client, private


def select_tenant(app, organization):
    # Trusted server-side test injection; the HTTP caller has no tenant selector.
    app.state.trusted_context = RequestContext('user-' + organization, organization, 'ADMIN')


def test_api_lists_and_known_cross_tenant_ids(tenant_app):
    app, client, ids = tenant_app
    state = client.get('/api/state').json()
    assert ids['ORG-A'] in {i['id'] for i in state['incidents']}
    assert ids['ORG-B'] not in {i['id'] for i in state['incidents']}
    assert 'ORG-A-UNIT' in {u['id'] for u in state['units']}
    assert 'ORG-B-UNIT' not in {u['id'] for u in state['units']}
    for path in [f"/api/incidents/{ids['ORG-B']}", '/api/units/ORG-B-UNIT',
                 f"/api/incidents/{ids['ORG-B']}/nearby-units"]:
        assert client.get(path).status_code == 404
    assert client.patch(f"/api/incidents/{ids['ORG-B']}",json={'title':'Stolen'}).status_code == 404
    assert client.post('/api/dispatches',json={'incident_id':ids['ORG-A'],'unit_id':'ORG-B-UNIT'}).status_code == 404
    assert client.post('/api/dispatches',json={'incident_id':ids['ORG-B'],'unit_id':'FIRE-001'}).status_code == 404
    nearby = client.get(f"/api/incidents/{ids['ORG-A']}/nearby-units").json()
    assert 'ORG-B-UNIT' not in {u['unit_id'] for u in nearby}
    assert ids['ORG-B'] not in {a['entity_id'] for a in client.get('/api/activity').json()}
    response = client.get('/api/state?organization_id=ORG-B',headers={'X-Organization-ID':'ORG-B'})
    assert ids['ORG-B'] not in {i['id'] for i in response.json()['incidents']}


def test_tenant_reset_and_dispatch_reads(tenant_app):
    app, client, ids = tenant_app
    select_tenant(app,'ORG-B')
    dispatch = client.post('/api/dispatches',json={'incident_id':ids['ORG-B'],'unit_id':'ORG-B-UNIT'}).json()
    before = client.get('/api/state').json()
    select_tenant(app,'ORG-A')
    assert client.get('/api/dispatches/' + dispatch['id']).status_code == 404
    assert dispatch['id'] not in {d['id'] for d in client.get('/api/dispatches').json()}
    assert client.post('/api/demo/reset').status_code == 200
    select_tenant(app,'ORG-B')
    assert client.get('/api/state').json() == before


def test_websocket_broadcasts_only_within_tenant(tenant_app):
    app, client, ids = tenant_app
    with client.websocket_connect('/ws/events') as a:
        ready_a = a.receive_json()
        select_tenant(app,'ORG-B')
        with client.websocket_connect('/ws/events') as b:
            ready_b = b.receive_json()
            assert ready_a['stream_id'] != ready_b['stream_id']
            assert client.patch('/api/incidents/'+ids['ORG-B'],json={'title':'B event'}).status_code == 200
            assert b.receive_json()['data']['id'] == ids['ORG-B']
            select_tenant(app,'ORG-A')
            assert client.patch('/api/incidents/'+ids['ORG-A'],json={'title':'A event'}).status_code == 200
            assert a.receive_json()['data']['id'] == ids['ORG-A']
            assert a.receive_json()['type'] == 'activity.created'
            assert b.receive_json()['type'] == 'activity.created'


def test_no_identity_fails_closed(tenant_app):
    app,client,_ = tenant_app
    app.state.trusted_context = None
    assert client.get('/api/incidents',headers={'X-Organization-ID':'ORG-A'}).status_code == 401
    assert client.get('/health').status_code == 200


def test_recovery_retains_both_tenant_contexts(pg_url):
    async def scenario():
        dispatch_ids = {}
        for organization in ['ORG-A','ORG-B']:
            repo = PostgresRepository(pg_url, organization_id=organization)
            await repo.open()
            await seed(repo)
            service = OperationsService(repo,ConnectionManager())
            await service.reset()
            dispatch_ids[organization] = (await service.create_dispatch(DispatchCreate(incident_id='INC-001',unit_id='FIRE-001'))).id
            await repo.close()
        app = create_app(database_url=pg_url, dispatch_delay=0,travel_duration=.03,tick_interval=.005)
        async with app.router.lifespan_context(app):
            for organization, identifier in dispatch_ids.items():
                service = await app.state.tenants.get(organization)
                await asyncio.wait_for(asyncio.gather(*tuple(service.simulator.tasks.values())),3)
                assert (await service.get_dispatch(identifier)).status == 'arrived'
                assert (await service.get_incident('INC-001')).status == 'on_scene'
                other = next(value for key,value in dispatch_ids.items() if key != organization)
                assert other not in {d.id for d in (await service.snapshot())['dispatches']}
                assert all(a.entity_id != other for a in (await service.snapshot())['activity'])
    asyncio.run(scenario())
