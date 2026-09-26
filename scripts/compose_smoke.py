"""Bounded infrastructure smoke; inherits DOCKER_CONTEXT / Compose environment."""
import json
import subprocess
import time
import urllib.request
import os


def compose(*args):
    return subprocess.check_output(['docker', 'compose', *args], text=True)


def main():
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        rows = [json.loads(line) for line in compose('ps', '--all', '--format', 'json').splitlines()]
        by = {row['Service']: row for row in rows}
        if all(by.get(name, {}).get('Health') == 'healthy' for name in ['postgres','redis','api','frontend']):
            break
        time.sleep(2)
    else:
        raise RuntimeError('Container health deadline exceeded.')
    assert by['db-roles']['ExitCode'] == 0 and by['db-roles']['State'] == 'exited'
    assert by['migrate']['ExitCode'] == 0 and by['migrate']['State'] == 'exited'
    assert all(by[name]['State'] == 'running' for name in ['worker','publisher'])
    assert compose('exec','-T','api','id','-u').strip() == '10001'
    assert compose('exec','-T','frontend','id','-u').strip() == '101'
    assert 'PONG' in compose('exec','-T','redis','redis-cli','ping')
    assert 'postgis' in compose('exec','-T','postgres','psql','-U','emergency','-d','emergency_platform','-Atc',"SELECT extname FROM pg_extension WHERE extname='postgis'")
    role_check = compose('exec','-T','postgres','psql','-U','emergency','-d','emergency_platform','-Atc',
        "SELECT rolname || ':' || rolsuper FROM pg_roles WHERE rolname IN ('emergency_app','emergency_migrator') ORDER BY rolname")
    assert role_check.strip().splitlines() == ['emergency_app:false', 'emergency_migrator:false']
    runtime_check = '''import asyncio
from sqlalchemy import text
from app.db.session import Database
async def check():
    database = Database()
    try:
        async with database.sessions() as session:
            result = (await session.execute(text("SELECT current_user, has_schema_privilege(current_user, 'public', 'CREATE'), has_table_privilege(current_user, 'incidents', 'SELECT'), has_table_privilege(current_user, 'incidents', 'INSERT')"))).one()
        assert result == ("emergency_app", False, True, True), result
    finally:
        await database.close()
asyncio.run(check())'''
    compose('exec','-T','api','python','-c',runtime_check)
    result=compose('exec','-T','worker','celery','-A','app.workers.celery_app:celery_app','inspect','ping','--timeout=5')
    assert 'pong' in result
    with urllib.request.urlopen(os.environ.get('BASE_URL','http://127.0.0.1:8080'), timeout=5) as response:
        assert response.status==200 and b'<html' in response.read()
    print('PASS Compose health, migration, PostGIS, Redis, real worker ping, publisher and frontend.')


if __name__=='__main__':main()
