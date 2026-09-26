import asyncio
from concurrent.futures import ThreadPoolExecutor
import re

import pytest

from app.domain.transitions import DomainError, require_transition
from app.schemas.operations import DispatchCreate


def dispatch(client, incident_id="INC-001", unit_id="FIRE-001"):
    return client.post("/api/dispatches", json={"incident_id": incident_id, "unit_id": unit_id})


def test_health_and_docs(client):
    assert client.get("/health").json() == {"status": "ok"}
    docs = client.get("/docs")
    assert docs.status_code == 200
    nonce = re.search(r'<script nonce="([^"]+)">', docs.text).group(1)
    csp = docs.headers["content-security-policy"]
    assert f"'nonce-{nonce}'" in csp
    assert "https://cdn.jsdelivr.net" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert "url: '/openapi.json'" in docs.text
    assert client.get("/openapi.json").json()["info"]["title"] == "Emergency Response Platform API"


@pytest.mark.parametrize("collection,identifier,count", [
    ("incidents", "INC-001", 6), ("units", "FIRE-001", 7), ("dispatches", "DSP-001", 1),
])
def test_list_get_and_missing(client, collection, identifier, count):
    response = client.get(f"/api/{collection}")
    assert response.status_code == 200
    assert len(response.json()) == count
    assert client.get(f"/api/{collection}/{identifier}").json()["id"] == identifier
    missing = client.get(f"/api/{collection}/missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"].endswith("_NOT_FOUND")


def test_create_incident_and_activity(client):
    body = {"type": "medical", "severity": "high", "description": " Needs help ",
            "latitude": 1.3, "longitude": 103.8, "title": "Medical call", "area": "Marina Bay"}
    response = client.post("/api/incidents", json=body)
    assert response.status_code == 201
    incident = response.json()
    assert incident["status"] == "active"
    assert incident["description"] == "Needs help"
    assert incident["assigned_unit_id"] is None
    assert incident["created_at"].endswith("Z")
    assert len(client.get("/api/incidents").json()) == 7
    event = client.get("/api/activity").json()[0]
    assert event["entity_id"] == incident["id"]
    assert event["event_type"] == "incident.created"


@pytest.mark.parametrize("changes", [
    {"latitude": 91}, {"longitude": -181}, {"type": "unknown"}, {"severity": "urgent"},
    {"description": "   "}, {"description": None}, {"status": "resolved"},
    {"description": "a" * 2001}, {"title": " "},
])
def test_incident_request_validation(client, changes):
    body = {"type": "medical", "severity": "high", "description": "Needs help",
            "latitude": 1.3, "longitude": 103.8, **changes}
    response = client.post("/api/incidents", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(client.get("/api/incidents").json()) == 6


def test_valid_dispatch_atomic_state_and_activity(client):
    response = dispatch(client)
    assert response.status_code == 201
    record = response.json()
    assert record["status"] == "created" and record["started_at"] is None
    state = client.get("/api/state").json()
    incident = next(item for item in state["incidents"] if item["id"] == "INC-001")
    unit = next(item for item in state["units"] if item["id"] == "FIRE-001")
    assert incident["status"] == unit["status"] == "dispatched"
    assert incident["assigned_unit_id"] == unit["id"]
    assert unit["assigned_incident_id"] == incident["id"]
    assert state["activity"][0]["metadata"]["dispatch_id"] == record["id"]


@pytest.mark.parametrize("incident,unit,status,code", [
    ("INC-001", "POL-002", 409, "UNIT_NOT_AVAILABLE"),
    ("INC-001", "AMB-002", 409, "UNIT_NOT_AVAILABLE"),
    ("INC-006", "FIRE-001", 409, "INCIDENT_NOT_ACTIVE"),
    ("missing", "FIRE-001", 404, "INCIDENT_NOT_FOUND"),
    ("INC-001", "missing", 404, "UNIT_NOT_FOUND"),
])
def test_invalid_dispatch_does_not_mutate(client, incident, unit, status, code):
    before = client.get("/api/state").json()
    response = dispatch(client, incident, unit)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert client.get("/api/state").json() == before


def test_duplicate_dispatch_and_assigned_unit_rejected(client):
    assert dispatch(client).status_code == 201
    assert dispatch(client).status_code == 409
    assert dispatch(client, "INC-002", "FIRE-001").json()["error"]["code"] == "UNIT_NOT_AVAILABLE"
    assert len(client.get("/api/dispatches").json()) == 2


def test_concurrent_dispatch_only_one_wins(client):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(dispatch, client, identifier, "FIRE-001")
                   for identifier in ["INC-001", "INC-002"]]
        assert sorted(future.result().status_code for future in futures) == [201, 409]
    assert len(client.get("/api/dispatches").json()) == 2


def test_safe_patch_and_assigned_coordinates(client):
    response = client.patch("/api/incidents/INC-001", json={"severity": "low", "title": "Updated"})
    assert response.status_code == 200
    assert response.json()["title"] == "Updated"
    assert dispatch(client).status_code == 201
    assert client.patch("/api/incidents/INC-001", json={"latitude": 1.4}).status_code == 409
    assert client.patch("/api/incidents/INC-001", json={"description": "More detail"}).status_code == 200
    assert client.patch("/api/incidents/INC-006", json={"title": "Changed"}).status_code == 409


@pytest.mark.parametrize("body", [{"status": "resolved"}, {"assigned_unit_id": "FIRE-001"}, {}, {"title": None}])
def test_patch_cannot_bypass_transitions(client, body):
    response = client.patch("/api/incidents/INC-001", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_resolve_arrived_incident_completes_dispatch_releases_unit(client):
    assert client.post("/api/incidents/INC-005/resolve").json()["status"] == "resolved"
    unit = client.get("/api/units/AMB-002").json()
    assert unit["status"] == "available" and unit["assigned_incident_id"] is None
    assert client.get("/api/dispatches/DSP-001").json()["status"] == "completed"
    assert client.post("/api/incidents/INC-005/resolve").status_code == 400
    assert client.post("/api/incidents/INC-001/resolve").status_code == 400


def test_service_lifecycle_and_invalid_arrival(app):
    async def scenario():
        service = app.state.service
        record = await service.create_dispatch(DispatchCreate(incident_id="INC-001", unit_id="FIRE-001"))
        before = await service.snapshot()
        with pytest.raises(DomainError, match="Cannot transition"):
            await service.arrive_dispatch(record.id)
        assert await service.snapshot() == before
        await service.start_dispatch(record.id)
        await service.update_location(record.id, 1.29, 103.85)
        await service.arrive_dispatch(record.id)
        unit, incident = await service.get_unit("FIRE-001"), await service.get_incident("INC-001")
        assert unit.status == incident.status == "on_scene"
        assert (unit.latitude, unit.longitude) == (incident.latitude, incident.longitude)
        await service.resolve_incident(incident.id)
        assert (await service.get_dispatch(record.id)).status == "completed"
        assert (await service.get_unit(unit.id)).status == "available"
    asyncio.run(scenario())


def test_invalid_transition_function():
    require_transition("unit", "available", "dispatched")
    with pytest.raises(DomainError):
        require_transition("unit", "offline", "dispatched")
    with pytest.raises(DomainError):
        require_transition("incident", "resolved", "dispatched")


def test_reset_restores_every_collection_and_is_idempotent(client):
    seed = client.get("/api/state").json()
    dispatch(client)
    client.post("/api/incidents/INC-005/resolve")
    first_reset = client.post("/api/demo/reset").json()
    second_reset = client.post("/api/demo/reset").json()
    for key in ["incidents", "units", "dispatches", "activity", "stream_id"]:
        assert first_reset[key] == second_reset[key] == seed[key]
    assert second_reset["sequence"] > first_reset["sequence"] > seed["sequence"]


def test_cors_is_limited_to_local_frontend(client):
    headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
               "Access-Control-Request-Headers": "content-type"}
    response = client.options("/api/dispatches", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    headers["Origin"] = "https://untrusted.example"
    assert "access-control-allow-origin" not in client.options("/api/dispatches", headers=headers).headers


def test_http_errors_use_same_envelope(client):
    assert client.get("/api/missing").json() == {"error": {"code": "HTTP_ERROR", "message": "Not Found"}}
    assert client.delete("/api/units/FIRE-001").status_code == 405


def test_snapshot_cannot_mutate_repository(app):
    async def scenario():
        snapshot = await app.state.service.snapshot()
        snapshot["incidents"][0].title = "Changed outside service"
        assert (await app.state.service.get_incident("INC-001")).title == "Building fire"
    asyncio.run(scenario())
