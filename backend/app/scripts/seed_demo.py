"""Seed an empty demo organization; never overwrite existing operational data."""
import asyncio
from sqlalchemy import exists, select, text
from sqlalchemy.dialects.postgresql import insert
from app.db.models import Organization, IncidentRecord, UnitRecord, DispatchRecord, ActivityRecord
from app.repositories.postgres import PostgresRepository


async def seed(repository: PostgresRepository) -> bool:
    # Explicit seeding is also serialized when two setup commands run together.
    async with repository.database.sessions.begin() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(514005)"))
        await session.execute(insert(Organization).values(id=repository.organization_id, name='Emergency Response Demo')
                              .on_conflict_do_nothing(index_elements=['id']))
    async with repository.transaction(write=True):
        for model in [IncidentRecord, UnitRecord, DispatchRecord, ActivityRecord]:
            if await repository.session.scalar(select(exists().where(model.organization_id == repository.organization_id))):
                return False
        await repository.reset()
    return True


async def seed_users(repository):
    from app.auth.config import AuthSettings
    from app.auth.crypto import hasher
    from app.db.models import UserRecord
    from starlette.concurrency import run_in_threadpool
    settings = AuthSettings.load()
    if settings.environment not in {'development', 'test'}:
        raise RuntimeError('Fictional demo users may only be seeded in development/test.')
    encoded = await run_in_threadpool(hasher.hash, 'Demo-Only-Response-2026!')
    async with repository.database.sessions.begin() as session:
        await session.execute(text('SELECT pg_advisory_xact_lock(514005)'))
        for role in ['ADMIN', 'DISPATCHER', 'RESPONDER']:
            await session.execute(insert(UserRecord).values(id='DEMO-' + role,
                organization_id=repository.organization_id,email=role.lower()+'@demo.example.com',
                display_name='Demo '+role.title(),password_hash=encoded,role=role,is_active=True)
                .on_conflict_do_nothing(index_elements=['email']))


async def main():
    repository = PostgresRepository()
    await repository.open()
    try:
        print('Demo seeded.' if await seed(repository) else 'Demo already contains data; left unchanged.')
        await seed_users(repository)
        print('Development demo accounts ensured; existing credentials were not changed.')
    finally:
        await repository.close()


if __name__ == '__main__':
    asyncio.run(main())
