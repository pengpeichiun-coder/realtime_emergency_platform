"""Two-process fanout, tenant isolation, Redis recovery, and API failover checks.
Run Compose --profile distributed first. Creates isolated fictional tenants.
"""
import asyncio
import json
import os
import secrets
import subprocess
import sys
from uuid import uuid4
import aiohttp

FIRST = os.environ.get('BASE_URL', 'http://127.0.0.1:8080')
SECOND = os.environ.get('SECOND_API_URL', 'http://127.0.0.1:8002')


async def main():
    async with aiohttp.ClientSession() as session:
        async def request(base, method, path, token=None, body=None, expected=200):
            async with session.request(method, base+path, json=body,
                    headers={'Authorization': 'Bearer '+token} if token else {}) as response:
                assert response.status == expected, (path, response.status)
                assert response.headers.get('X-Request-ID')
                return await response.json()
        async def register():
            return (await request(FIRST, 'POST', '/api/auth/register', body={
                'organization_name': 'Distributed smoke '+uuid4().hex, 'display_name': 'Smoke',
                'email': uuid4().hex+'@example.com', 'password': secrets.token_urlsafe(24)}, expected=201))['access_token']
        a, b = await register(), await register()
        async def connect(base, token):
            ws = await session.ws_connect(base.replace('http', 'ws', 1)+'/ws/events', origin=FIRST)
            await ws.send_json({'type': 'authenticate', 'access_token': token})
            ready = await ws.receive_json(timeout=10)
            assert ready['type'] == 'connection.ready'
            return ws, ready
        wa, ca = await connect(FIRST, a)
        wb, cb = await connect(SECOND, a)
        other, _ = await connect(SECOND, b)
        assert (ca['stream_id'], ca['sequence']) == (cb['stream_id'], cb['sequence'])
        body = {'type': 'fire', 'severity': 'high', 'description': 'Fictional cross-process validation',
                'latitude': 1.29, 'longitude': 103.85}
        await request(FIRST, 'POST', '/api/incidents', a, body, 201)
        left = [await wa.receive_json(timeout=10) for _ in range(2)]
        right = [await wb.receive_json(timeout=10) for _ in range(2)]
        assert left == right and len({event['sequence'] for event in left}) == 2
        assert {event['type'] for event in left} == {'incident.created', 'activity.created'}
        for socket in (wa, wb, other):
            try:
                await socket.receive(timeout=.3)
            except TimeoutError:
                pass
            else:
                raise AssertionError('Duplicate or cross-tenant event')
        await request(SECOND, 'POST', '/api/incidents', b, body, 201)
        for _ in range(2):
            assert (await other.receive_json(timeout=10))['stream_id'] != ca['stream_id']
        for socket in (wa, wb):
            try:
                await socket.receive(timeout=.3)
            except TimeoutError:
                pass
            else:
                raise AssertionError('Cross-tenant event')
        await wb.close()
        wb, ready = await connect(SECOND, a)
        snapshot = await request(SECOND, 'GET', '/api/state', a)
        assert snapshot['stream_id'] == ready['stream_id'] == ca['stream_id']
        assert snapshot['sequence'] == ready['sequence'] == max(x['sequence'] for x in left)
        assert len(snapshot['incidents']) == 1
        if '--outage' in sys.argv:
            # Only this Compose project's Redis is stopped; always restore it.
            def compose(*args):
                subprocess.run(['docker', 'compose', *args], check=True, capture_output=True)
            try:
                await asyncio.to_thread(compose, 'stop', 'redis')
                await request(SECOND, 'GET', '/health/live')
                await request(SECOND, 'GET', '/health/ready', expected=503)
                missed = await request(SECOND, 'POST', '/api/incidents', a, body, 201)
            finally:
                await asyncio.to_thread(compose, 'start', 'redis')
            for _ in range(30):
                async with session.get(SECOND+'/health/ready') as response:
                    if response.status == 200:
                        break
                await asyncio.sleep(1)
            else:
                raise AssertionError('Redis subscriber did not recover')
            recovered, cursor = await connect(SECOND, a)
            if not wb.closed:
                await wb.close()
            wb = recovered
            state = await request(SECOND, 'GET', '/api/state', a)
            assert missed['id'] in {item['id'] for item in state['incidents']}
            assert state['sequence'] == cursor['sequence']
            print('PASS Redis outage: live 200, ready 503, committed state survives, subscription and REST resync recover.')
        if '--api-failure' in sys.argv:
            def compose(*args):
                subprocess.run(['docker', 'compose', *args], check=True, capture_output=True)

            failure_id = None
            try:
                await asyncio.to_thread(compose, 'stop', 'api')
                async with session.get(SECOND + '/health/ready', timeout=5) as response:
                    assert response.status == 200
                failure = await request(SECOND, 'POST', '/api/incidents', a, {
                    'type': 'medical', 'severity': 'low',
                    'description': 'Fictional API failover validation',
                    'latitude': 1.29, 'longitude': 103.85,
                }, 201)
                failure_id = failure['id']
                received = [await wb.receive_json(timeout=10) for _ in range(2)]
                assert {item['data'].get('id') for item in received} >= {failure_id}
                state = await request(SECOND, 'GET', '/api/state', a)
                assert failure_id in {item['id'] for item in state['incidents']}
            finally:
                await asyncio.to_thread(compose, 'start', 'api')

            for _ in range(40):
                rows = await asyncio.to_thread(
                    subprocess.check_output,
                    ['docker', 'compose', 'ps', '--format', 'json'],
                    text=True,
                )
                if any(
                    row.get('Service') == 'api' and row.get('Health') == 'healthy'
                    for row in (json.loads(line) for line in rows.splitlines())
                ):
                    break
                await asyncio.sleep(.5)
            else:
                raise AssertionError('Stopped API instance did not recover')
            wa, ready = await connect(FIRST, a)
            state = await request(FIRST, 'GET', '/api/state', a)
            assert state['stream_id'] == ready['stream_id']
            if failure_id:
                assert failure_id in {item['id'] for item in state['incidents']}
            print('PASS API instance failure: surviving replica stays healthy, commits persist, restarted replica resyncs the same state.')
        for socket in (wa, wb, other):
            if not socket.closed:
                await socket.close()
        print('PASS real Redis two-API fanout, no duplicates, bidirectional tenant isolation, shared cursor and reconnect REST resync.')


if __name__ == '__main__':
    asyncio.run(main())
