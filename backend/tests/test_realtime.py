import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.repositories.memory import MemoryRepository
from app.schemas.events import RealtimeEvent
from app.schemas.operations import DispatchCreate
from app.services.connections import ConnectionManager

event_schema = TypeAdapter(RealtimeEvent)


def receive(socket):
    event = socket.receive_json()
    event_schema.validate_python(event)
    assert event["timestamp"].endswith("Z")
    return event


def test_websocket_handshake_and_snapshot_cursor(client):
    with client.websocket_connect("/ws/events") as socket:
        ready = receive(socket)
        state = client.get("/api/state").json()
        assert ready["type"] == "connection.ready" and ready["data"] == {}
        assert (ready["stream_id"], ready["sequence"]) == (state["stream_id"], state["sequence"])


def test_incident_events_and_resolve_payloads(client):
    with client.websocket_connect("/ws/events") as socket:
        ready = receive(socket)
        incident = client.post("/api/incidents", json={
            "type": "medical", "severity": "high", "description": "New call",
            "latitude": 1.3, "longitude": 103.8,
        }).json()
        events = [receive(socket) for _ in range(2)]
        assert [event["type"] for event in events] == ["incident.created", "activity.created"]
        assert events[0]["data"] == incident
        client.patch(f"/api/incidents/{incident['id']}", json={"title": "Revised call"})
        events.extend(receive(socket) for _ in range(2))
        assert events[2]["type"] == "incident.updated"
        assert events[2]["data"]["title"] == "Revised call"
        assert client.post("/api/incidents/INC-005/resolve").status_code == 200
        events.extend(receive(socket) for _ in range(4))
        assert [event["type"] for event in events[4:]] == [
            "incident.resolved", "unit.status.updated", "dispatch.completed", "activity.created",
        ]
        assert events[5]["data"]["status"] == "available"
        assert events[6]["data"]["status"] == "completed"
        assert events[6]["data"]["completed_at"].endswith("Z")
        assert [event["sequence"] for event in events] == list(range(ready["sequence"] + 1, ready["sequence"] + 9))
        assert client.get("/api/state").json()["sequence"] == events[-1]["sequence"]


def test_backend_simulation_broadcasts_complete_lifecycle():
    app = create_app(repository=MemoryRepository(), dispatch_delay=0.01, travel_duration=0.04, tick_interval=0.005)
    with TestClient(app) as client, client.websocket_connect("/ws/events") as socket:
        ready = receive(socket)
        dispatch = client.post("/api/dispatches", json={"incident_id": "INC-001", "unit_id": "FIRE-001"}).json()
        events = []
        while True:
            event = receive(socket)
            events.append(event)
            if event["type"] == "activity.created" and "arrived on scene" in event["data"]["message"]:
                break
        assert "dispatch.created" in {event["type"] for event in events}
        statuses = [event["data"]["status"] for event in events if event["type"] == "unit.status.updated"]
        assert statuses == ["dispatched", "en_route", "on_scene"]
        locations = [event["data"] for event in events if event["type"] == "unit.location.updated"]
        assert len(locations) >= 2
        incident = client.get("/api/incidents/INC-001").json()
        assert (locations[-1]["latitude"], locations[-1]["longitude"]) == (incident["latitude"], incident["longitude"])
        assert incident["status"] == "on_scene" and incident["response_time_seconds"] is None
        assert client.get(f"/api/dispatches/{dispatch['id']}").json()["status"] == "arrived"
        assert all(event["stream_id"] == ready["stream_id"] for event in events)
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert not app.state.service.simulator.tasks
    assert not app.state.connections.tasks
    assert not app.state.connections.connections


def test_concurrent_simulations_and_duplicate_task_prevention():
    async def scenario():
        app = create_app(repository=MemoryRepository(), dispatch_delay=0.001, travel_duration=0.03, tick_interval=0.005)
        service = app.state.service
        first, second = await asyncio.gather(
            service.create_dispatch(DispatchCreate(incident_id="INC-001", unit_id="FIRE-001")),
            service.create_dispatch(DispatchCreate(incident_id="INC-002", unit_id="AMB-001")),
        )
        assert len(service.simulator.tasks) == 2
        assert service.simulator.start(first.id) is False
        await asyncio.wait_for(asyncio.gather(*tuple(service.simulator.tasks.values())), timeout=1)
        assert (await service.get_dispatch(first.id)).status == "arrived"
        assert (await service.get_dispatch(second.id)).status == "arrived"
        assert not service.simulator.tasks
        await service.shutdown()
    asyncio.run(scenario())


def test_reset_cancels_tasks_and_never_emits_stale_movement():
    async def scenario():
        app = create_app(repository=MemoryRepository(), dispatch_delay=0, travel_duration=0.3, tick_interval=0.005)
        service = app.state.service
        record = await service.create_dispatch(DispatchCreate(incident_id="INC-001", unit_id="FIRE-001"))
        for _ in range(50):
            if (await service.get_dispatch(record.id)).status == "en_route":
                break
            await asyncio.sleep(0.001)
        assert (await service.get_dispatch(record.id)).status == "en_route"
        running = tuple(service.simulator.tasks.values())
        reset = await service.reset()
        assert all(task.done() for task in running)
        assert not service.simulator.tasks
        await asyncio.sleep(0.03)
        assert await service.snapshot() == reset
        assert not any(task.get_name().startswith("dispatch-") for task in asyncio.all_tasks())
        await service.shutdown()
    asyncio.run(scenario())


def test_reset_broadcast_has_matching_sequence_and_stable_stream(client):
    with client.websocket_connect("/ws/events") as socket:
        ready = receive(socket)
        first = client.post("/api/demo/reset").json()
        reset_event = receive(socket)
        assert reset_event["type"] == "state.reset"
        assert reset_event["data"] == first
        assert reset_event["sequence"] == first["sequence"] == ready["sequence"] + 1
        assert first["stream_id"] == ready["stream_id"]
        second = client.post("/api/demo/reset").json()
        assert receive(socket)["sequence"] == second["sequence"] == first["sequence"] + 1


def test_disconnecting_one_client_does_not_break_other_client(client):
    with client.websocket_connect("/ws/events") as remaining:
        receive(remaining)
        with client.websocket_connect("/ws/events") as leaving:
            receive(leaving)
        client.post("/api/demo/reset")
        assert receive(remaining)["type"] == "state.reset"


def test_shutdown_cancels_simulation_tasks():
    app = create_app(repository=MemoryRepository(), dispatch_delay=10)
    with TestClient(app) as client:
        assert client.post("/api/dispatches", json={"incident_id": "INC-001", "unit_id": "FIRE-001"}).status_code == 201
        tasks = tuple(app.state.service.simulator.tasks.values())
        assert len(tasks) == 1
    assert all(task.done() for task in tasks)
    assert not app.state.service.simulator.tasks


def test_new_application_has_new_stream_identity():
    first = create_app(repository=MemoryRepository(), simulation_enabled=False)
    second = create_app(repository=MemoryRepository(), simulation_enabled=False)
    assert first.state.service.stream_id != second.state.service.stream_id


def test_websocket_rejects_unapproved_browser_origin(client):
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/ws/events", headers={"Origin": "https://untrusted.example"}):
            pass
    assert error.value.code == 1008


def test_event_schema_rejects_mismatched_payload_and_naive_timestamp(client):
    with client.websocket_connect("/ws/events") as socket:
        ready = receive(socket)
    with pytest.raises(ValidationError):
        event_schema.validate_python({**ready, "type": "unit.location.updated", "data": {"latitude": 1.3}})
    with pytest.raises(ValidationError):
        event_schema.validate_python({**ready, "timestamp": "2026-09-22T00:00:00"})


class FakeSocket:
    def __init__(self, blocked=False):
        self.sent = []
        self.close_code = None
        self.incoming = asyncio.Queue()
        self.send_gate = asyncio.Event()
        if not blocked:
            self.send_gate.set()

    async def send_json(self, data):
        await self.send_gate.wait()
        self.sent.append(data)

    async def receive(self):
        return await self.incoming.get()

    async def close(self, code):
        self.close_code = code


def test_slow_subscriber_is_disconnected_without_blocking_others():
    async def scenario():
        manager = ConnectionManager(queue_size=2)
        service = create_app(repository=MemoryRepository(), simulation_enabled=False).state.service
        ready = service.connection_ready()
        slow, fast = FakeSocket(blocked=True), FakeSocket()
        slow_connection, fast_connection = manager.connect(slow, ready), manager.connect(fast, ready)
        runs = [asyncio.create_task(manager.run(item)) for item in [slow_connection, fast_connection]]
        await asyncio.sleep(0.001)
        for _ in range(4):
            manager.broadcast(ready)
            await asyncio.sleep(0.001)
        await asyncio.wait_for(slow_connection.closed.wait(), timeout=1)
        assert slow.close_code == 1013
        assert len(fast.sent) == 5
        assert fast_connection.id in manager.connections
        await asyncio.wait_for(manager.shutdown(), timeout=1)
        await asyncio.gather(*runs)
        assert fast.close_code == 1001
        assert not manager.tasks and not manager.connections
    asyncio.run(scenario())
