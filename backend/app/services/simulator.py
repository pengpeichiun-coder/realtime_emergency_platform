"""Demo movement with a PostgreSQL advisory lock per active dispatch. No browser timers own operational state."""

import asyncio
from sqlalchemy import text
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.operations import OperationsService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimulationConfig:
    dispatch_delay: float = 3.0
    travel_duration: float = 16.0
    tick_interval: float = 0.2

    def __post_init__(self) -> None:
        if (
            not all(
                math.isfinite(value)
                for value in (
                    self.dispatch_delay,
                    self.travel_duration,
                    self.tick_interval,
                )
            )
            or self.dispatch_delay < 0
            or self.travel_duration <= 0
            or self.tick_interval <= 0
        ):
            raise ValueError(
                "Simulation delay must be nonnegative; travel duration and tick interval must be positive."
            )


class MovementSimulator:
    def __init__(self, service: "OperationsService", config: SimulationConfig):
        self.service = service
        self.config = config
        self.tasks: dict[str, asyncio.Task] = {}

    def start(self, dispatch_id: str) -> bool:
        # Registration is synchronous and occurs inside the service mutation lock.
        if dispatch_id in self.tasks:
            return False
        task = asyncio.create_task(
            self._run(dispatch_id), name=f"dispatch-{dispatch_id}"
        )
        self.tasks[dispatch_id] = task
        task.add_done_callback(lambda completed: self._forget(dispatch_id, completed))
        return True

    def _forget(self, dispatch_id: str, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            logger.warning("Simulation ownership connection failed")
        if self.tasks.get(dispatch_id) is task:
            self.tasks.pop(dispatch_id)

    async def _run(self, dispatch_id: str) -> None:
        database = getattr(self.service.repository, "database", None)
        if database is None:
            return await self._move(dispatch_id)
        async with database.lock_engine.connect() as connection:
            key = self.service.repository.organization_id + ":" + dispatch_id
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:id, 1201))"),
                {"id": key},
            )
            await connection.commit()
            if not acquired:
                return
            try:
                await self._move(dispatch_id)
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:id, 1201))"),
                    {"id": key},
                )
                await connection.commit()

    async def _move(self, dispatch_id: str) -> None:
        try:
            dispatch = await self.service.get_dispatch(dispatch_id)
            if dispatch.status == "created":
                age = max(0, (datetime.now(UTC) - dispatch.created_at).total_seconds())
                await asyncio.sleep(max(0, self.config.dispatch_delay - age))
                dispatch = await self.service.start_dispatch(dispatch_id)
            elif dispatch.status != "en_route":
                return
            incident = await self.service.get_incident(dispatch.incident_id)
            logger.info("Simulation started: %s", dispatch_id)
            loop = asyncio.get_running_loop()
            # Resume from the latest committed location for the remaining wall-clock
            # duration. Long downtime finishes the trip immediately, without rewinding.
            unit = await self.service.get_unit(dispatch.unit_id)
            age = max(0, (datetime.now(UTC) - dispatch.started_at).total_seconds())
            remaining = max(0, self.config.travel_duration - age)
            started = loop.time()
            while True:
                progress = (
                    min(1.0, (loop.time() - started) / remaining) if remaining else 1.0
                )
                if progress >= 1:
                    await self.service.arrive_dispatch(dispatch_id)
                    logger.info("Simulation completed: %s", dispatch_id)
                    return
                await self.service.update_location(
                    dispatch_id,
                    unit.latitude + (incident.latitude - unit.latitude) * progress,
                    unit.longitude + (incident.longitude - unit.longitude) * progress,
                )
                await asyncio.sleep(
                    min(self.config.tick_interval, remaining * (1 - progress))
                )
        except asyncio.CancelledError:
            logger.info("Simulation cancelled: %s", dispatch_id)
            raise
        except Exception:
            logger.exception("Simulation failed: %s", dispatch_id)

    async def cancel_all(self) -> None:
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
