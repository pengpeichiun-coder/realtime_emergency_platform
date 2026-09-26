"""Explicit database configuration; imports never connect to a database."""
import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy.engine import make_url


def database_url() -> str:
    load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
    value = os.environ.get('DATABASE_URL')
    if not value:
        raise RuntimeError('DATABASE_URL is required. Configure backend/.env, run migrations, then seed the demo.')
    if make_url(value).drivername != 'postgresql+asyncpg':
        raise ValueError('DATABASE_URL must use postgresql+asyncpg.')
    return value
