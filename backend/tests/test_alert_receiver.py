import importlib.util
import json
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("local_alert_receiver", ROOT / "scripts/alert_receiver.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def receiver():
    server = module.AlertReceiver(("127.0.0.1", 0))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def send(base, payload):
    return urlopen(Request(base + "/alerts", data=json.dumps(payload).encode(),
                           headers={"Content-Type": "application/json", "Authorization": "secret-header"}), timeout=5)


def test_receiver_captures_firing_resolved_and_drops_unapproved_metadata(receiver):
    for status in ("firing", "resolved"):
        with send(receiver, {"alerts": [{"status": status, "labels": {
            "alertname": "EmergencyOutboxPublisherUnavailable", "cluster": "local",
            "organization_id": "private-tenant", "token": "secret-label",
        }, "annotations": {"summary": "Publisher unavailable", "credential": "secret-annotation"}}]}) as response:
            assert response.status == 200
    with urlopen(receiver + "/events", timeout=5) as response:
        body = response.read().decode()
    events = json.loads(body)
    assert [event["status"] for event in events] == ["firing", "resolved"]
    assert all(event["alertname"] == "EmergencyOutboxPublisherUnavailable" for event in events)
    assert "secret" not in body and "private-tenant" not in body


@pytest.mark.parametrize("payload,status", [
    ({}, 400), ({"alerts": "invalid"}, 400),
    ({"alerts": [{"status": "unknown", "labels": {"alertname": "test"}}]}, 400),
    ({"alerts": [{}] * 21}, 400), ({"padding": "x" * 65536}, 413),
])
def test_receiver_rejects_invalid_or_unbounded_requests(receiver, payload, status):
    with pytest.raises(HTTPError) as error:
        send(receiver, payload)
    assert error.value.code == status
