"""One engine per application, one AsyncSession per repository transaction."""

import os
import ssl
from pathlib import Path
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.db.config import database_url


def database_connect_args():
    ca_file = os.environ.get("DB_SSL_CA_FILE")
    if not ca_file:
        return {}
    return {"ssl": ssl.create_default_context(cafile=str(Path(ca_file)))}


class Database:
    def __init__(self, url: str | None = None):
        url = url or database_url()
        if make_url(url).drivername != "postgresql+asyncpg":
            raise ValueError("Only postgresql+asyncpg database URLs are supported.")
        connect_args = database_connect_args()
        self.engine = create_async_engine(
            url,
            connect_args=connect_args,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
        )
        self.lock_engine = create_async_engine(
            url, connect_args=connect_args, poolclass=NullPool
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self):
        await self.lock_engine.dispose()
        await self.engine.dispose()
