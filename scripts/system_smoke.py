"""Real HTTP/WebSocket/worker workflow against an isolated newly registered tenant.
Requires backend development dependencies; no secrets printed. Demo/test only.
"""
import asyncio
import json
import os
import secrets
from uuid import uuid4
import aiohttp

BASE = os.environ.get('BASE_URL', 'http://127.0.0.1:8080').rstrip('/')


async def main():
    async with aiohttp.ClientSession() as client:
        async def request(method,path,body=None,status=200):
            async with client.request(method, BASE+path, json=body) as response:
                if response.status!=status:raise AssertionError(f'{method} {path}: {response.status}')
                return await response.json() if status!=204 else None
        registered=await request('POST','/api/auth/register',{'organization_name':'Isolated smoke '+uuid4().hex,
            'display_name':'Smoke admin','email':uuid4().hex+'@example.com','password':secrets.token_urlsafe(24)},201)
        client.headers['Authorization']='Bearer '+registered['access_token']
        await request('POST','/api/demo/reset')
        async with client.ws_connect(BASE.replace('http','ws',1)+'/ws/events',origin=BASE) as ws:
            await ws.send_json({'type':'authenticate','access_token':registered['access_token']})
            assert (await ws.receive_json(timeout=10))['type']=='connection.ready'
            incident=await request('POST','/api/incidents',{'type':'fire','severity':'high','description':'Fictional container smoke',
                'title':'Container smoke','latitude':1.29,'longitude':103.85},201)
            found=False
            for _ in range(10):
                event=await ws.receive_json(timeout=10)
                if event['type']=='incident.created' and event['data']['id']==incident['id']:found=True;break
            assert found
            nearby=await request('GET',f"/api/incidents/{incident['id']}/nearby-units")
            assert nearby
            await request('POST','/api/dispatches',{'incident_id':incident['id'],'unit_id':'FIRE-001'},201)
            moved=False
            for _ in range(150):
                event=await ws.receive_json(timeout=30)
                moved |= event['type']=='unit.location.updated'
                if event['type']=='incident.updated' and event['data']['id']==incident['id'] and event['data']['status']=='on_scene':break
            else:raise AssertionError('Arrival deadline exceeded')
            assert moved
            await request('POST',f"/api/incidents/{incident['id']}/resolve")
        jobs=[]
        for path in [f"/api/incidents/{incident['id']}/reports",f"/api/incidents/{incident['id']}/ai-summary",'/api/ai/shift-handover']:
            jobs.append((await request('POST',path,status=202))['job_id'])
        for identifier in jobs:
            for _ in range(100):
                job=await request('GET','/api/jobs/'+identifier)
                if job['status']=='FAILED':raise AssertionError(job['error_code'])
                if job['status']=='SUCCEEDED':break
                await asyncio.sleep(.3)
            else:raise AssertionError('Job deadline exceeded')
            assert job['result_payload'] and job['attempt_count']==1
        print('PASS real same-origin auth, PostGIS, WebSocket dispatch/movement/resolve, report and both fake AI worker jobs.')


if __name__=='__main__':asyncio.run(main())
