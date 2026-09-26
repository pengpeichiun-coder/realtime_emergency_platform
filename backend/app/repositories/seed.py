"""Fictional Singapore demo data, matching the original frontend fixtures."""

from datetime import UTC, datetime

from app.domain.models import ActivityEvent, Dispatch, EmergencyUnit, Incident


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def seed_incidents() -> dict[str, Incident]:
    rows = [
        ("INC-001", "fire", "Building fire", "Marina Bay", "critical", "active", 1.2834, 103.8607,
         "Smoke reported on the upper floor of a commercial building. Evacuation is underway.", "2026-09-22T10:28:00+08:00", None, None),
        ("INC-002", "traffic", "Vehicle collision", "Orchard Road", "high", "active", 1.3042, 103.8328,
         "Two vehicles involved near the junction. One lane obstructed; injuries unconfirmed.", "2026-09-22T10:24:00+08:00", None, None),
        ("INC-003", "medical", "Medical assistance", "Raffles Place", "medium", "active", 1.2839, 103.8515,
         "A person requires medical assessment at the public plaza. A bystander is assisting.", "2026-09-22T10:21:00+08:00", None, None),
        ("INC-004", "security", "Public disturbance", "Bugis", "medium", "active", 1.3008, 103.8553,
         "A disturbance reported near the shopping precinct. Patrol assistance requested.", "2026-09-22T10:18:00+08:00", None, None),
        ("INC-005", "medical", "Pedestrian injury", "Chinatown", "high", "on_scene", 1.2838, 103.8433,
         "Ambulance crew is assessing an injured pedestrian near the MRT entrance.", "2026-09-22T10:12:00+08:00", "AMB-002", 186),
        ("INC-006", "fire", "Smoke investigation", "Tiong Bahru", "low", "resolved", 1.2865, 103.827,
         "Source of smoke identified and made safe. Crews cleared the location.", "2026-09-22T09:46:00+08:00", None, 254),
    ]
    return {
        row[0]: Incident(*row[:9], created_at=utc(row[9]), assigned_unit_id=row[10], response_time_seconds=row[11])
        for row in rows
    }


def seed_units() -> dict[str, EmergencyUnit]:
    rows = [
        ("FIRE-001", "Central Fire 01", "fire_truck", "available", "Hill Street", 1.2926, 103.8496, "2026-09-22T10:29:00+08:00", None),
        ("AMB-001", "Orchard Medic 01", "ambulance", "available", "Somerset", 1.3007, 103.8399, "2026-09-22T10:29:00+08:00", None),
        ("POL-001", "Rochor Patrol 01", "police", "available", "Rochor", 1.304, 103.8559, "2026-09-22T10:28:00+08:00", None),
        ("FIRE-002", "Central Fire 02", "fire_truck", "available", "Fort Canning", 1.2977, 103.8447, "2026-09-22T10:28:00+08:00", None),
        ("AMB-003", "Civic Medic 03", "ambulance", "available", "Civic District", 1.288, 103.8533, "2026-09-22T10:29:00+08:00", None),
        ("AMB-002", "Outram Medic 02", "ambulance", "on_scene", "Chinatown", 1.2838, 103.8433, "2026-09-22T10:15:06+08:00", "INC-005"),
        ("POL-002", "Cantonment Patrol 02", "police", "offline", "Cantonment", 1.2779, 103.8394, "2026-09-22T09:55:00+08:00", None),
    ]
    return {
        row[0]: EmergencyUnit(*row[:7], last_updated_at=utc(row[7]), assigned_incident_id=row[8])
        for row in rows
    }


def seed_dispatches() -> dict[str, Dispatch]:
    # The existing on-scene crew needs a corresponding arrived dispatch.
    return {"DSP-001": Dispatch(
        id="DSP-001", incident_id="INC-005", unit_id="AMB-002", status="arrived",
        created_at=utc("2026-09-22T10:12:00+08:00"),
        started_at=utc("2026-09-22T10:12:03+08:00"),
        arrived_at=utc("2026-09-22T10:15:06+08:00"),
        origin_latitude=1.2779, origin_longitude=103.8394,
    )}


def seed_activity() -> list[ActivityEvent]:
    return [ActivityEvent(
        id="ACT-001", event_type="demo.reset", entity_type="demo", entity_id="demo",
        message="Demo ready. Select an active incident to begin.",
        timestamp=utc("2026-09-22T10:30:00+08:00"),
    )]
