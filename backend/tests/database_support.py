"""Reject integration-test targets that could identify a development database."""
import os
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy.engine import make_url


def safe_test_url(value):
    url = make_url(value)
    if (url.drivername != 'postgresql+asyncpg' or url.query or url.host not in {'localhost', '127.0.0.1', '::1'}
            or not url.database or not url.database.endswith('_test')):
        raise ValueError('Integration tests require a local postgresql+asyncpg database ending in _test.')
    development = os.environ.get('DATABASE_URL') or dotenv_values(Path(__file__).resolve().parents[1] / '.env').get('DATABASE_URL')
    if development:
        other = make_url(development)
        # Credentials do not distinguish databases; localhost aliases are equivalent.
        local_hosts = {'localhost', '127.0.0.1', '::1', None}
        same_host = url.host == other.host or {url.host, other.host} <= local_hosts
        if same_host and (url.port or 5432) == (other.port or 5432) and url.database == other.database:
            raise ValueError('TEST_DATABASE_URL must not identify the development database.')
    return value
