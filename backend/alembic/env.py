"""Run PostgreSQL migrations online or emit reviewable offline SQL."""

import asyncio
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from app.db.session import database_connect_args
from sqlalchemy.pool import NullPool
from app.db.config import database_url
from app.db.models import Base

config = context.config
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name not in target_metadata.tables)


def migrate(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def online(url):
    engine = create_async_engine(
        url, connect_args=database_connect_args(), poolclass=NullPool
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


url = config.attributes.get("database_url") or database_url()
if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online(url))
