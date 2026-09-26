"""Single-process tenant services share a pool, but never state, locks or streams."""

import asyncio
import os
import logging
from app.services.event_bus import RedisEventBus
from sqlalchemy import select
from app.db.models import Organization
from app.db.session import Database
from app.domain.transitions import DomainError
from app.repositories.postgres import PostgresRepository
from app.services.connections import ConnectionManager
from app.services.operations import OperationsService
from app.services.simulator import MovementSimulator


class TenantServices:
    def __init__(self, url, simulation_config, realtime_enabled=False):
        self.realtime_enabled = realtime_enabled
        self.event_bus = None
        self.recovery_task = None
        self.url = url
        self.simulation_config = simulation_config
        self.database = None
        self.services = {}

    def build(self, repository):
        service = OperationsService(repository, ConnectionManager())
        service.event_bus = self.event_bus
        if self.simulation_config:
            service.simulator = MovementSimulator(service, self.simulation_config)
        return service

    async def get(self, organization_id):
        if organization_id not in self.services:
            async with self.database.sessions() as session:
                exists = await session.get(Organization, organization_id)
            if exists is None:
                raise DomainError(
                    "ORGANIZATION_NOT_FOUND", "Organization was not found.", 404
                )
            # No await between cache check and insertion, including after the DB lookup.
            if organization_id not in self.services:
                repository = PostgresRepository(
                    organization_id=organization_id, database=self.database
                )
                self.services[organization_id] = self.build(repository)
        return self.services[organization_id]

    async def open(self):
        self.database = Database(self.url)
        if self.realtime_enabled:
            self.event_bus = RedisEventBus(
                os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"), self.services
            )
            await self.event_bus.start()
        async with self.database.sessions() as session:
            ids = (await session.scalars(select(Organization.id))).all()
        for organization_id in ids:
            service = await self.get(organization_id)
            await service.resume()

        if self.realtime_enabled and self.simulation_config:
            self.recovery_task = asyncio.create_task(
                self.recover(), name="simulation-recovery"
            )

    async def recover(self):
        while True:
            await asyncio.sleep(5)
            try:
                async with self.database.sessions() as session:
                    ids = (await session.scalars(select(Organization.id))).all()
                for identifier in ids:
                    await (await self.get(identifier)).resume()
            except Exception:
                logging.getLogger(__name__).warning(
                    "Simulation recovery scan unavailable"
                )

    async def close(self):
        if self.recovery_task:
            self.recovery_task.cancel()
            await asyncio.gather(self.recovery_task, return_exceptions=True)
        for service in self.services.values():
            await service.shutdown()
        if self.event_bus:
            await self.event_bus.close()
        self.services.clear()
        if self.database:
            await self.database.close()
            self.database = None
