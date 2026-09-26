"""Development-only, bounded in-memory Alertmanager receiver. No forwarding."""

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock

LABELS = {"alertname", "cluster", "job", "job_type", "severity", "service"}
ANNOTATIONS = {"summary", "description", "runbook_url"}
MAX_BODY = 65536


class AlertReceiver(ThreadingHTTPServer):
    def __init__(self, address):
        super().__init__(address, Handler)
        self.events = deque(maxlen=100)
        self.events_lock = Lock()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # No headers, request bodies, URLs or credentials in logs.

    def respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"status": "ok"})
        elif self.path == "/events":
            with self.server.events_lock:
                events = list(self.server.events)
            self.respond(200, events)
        else:
            self.respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/alerts":
            return self.respond(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.respond(413, {"error": "body exceeds local receiver limit"})
            payload = json.loads(self.rfile.read(length))
            alerts = payload["alerts"]
            if not isinstance(alerts, list) or len(alerts) > 20:
                raise ValueError("invalid alert list")
            events = []
            for alert in alerts:
                status = alert["status"]
                if status not in {"firing", "resolved"}:
                    raise ValueError("invalid status")
                labels, annotations = alert["labels"], alert.get("annotations", {})
                if not isinstance(labels, dict) or not isinstance(annotations, dict):
                    raise ValueError("invalid metadata")
                if not isinstance(labels.get("alertname"), str):
                    raise ValueError("invalid alert name")
                events.append({
                    "alertname": str(labels["alertname"])[:160], "status": status,
                    "labels": {key: value[:500] for key, value in labels.items()
                               if key in LABELS and isinstance(value, str)},
                    "annotations": {key: value[:1000] for key, value in annotations.items()
                                    if key in ANNOTATIONS and isinstance(value, str)},
                })
        except (ValueError, TypeError, KeyError, TimeoutError):
            return self.respond(400, {"error": "invalid alert notification"})
        with self.server.events_lock:
            self.server.events.extend(events)
        for event in events:
            print(json.dumps({key: event[key] for key in ("alertname", "status")}), flush=True)
        self.respond(200, {"accepted": len(events)})


if __name__ == "__main__":
    with AlertReceiver(("0.0.0.0", 8080)) as server:
        server.serve_forever()
