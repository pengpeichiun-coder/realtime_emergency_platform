"""Real isolated publisher/worker outages; creates and drops only its own test DB.

Run with backend/.venv/bin/python. Uses a local TEST_DATABASE_URL for connection
credentials, but never writes that database. Requires permission to create a new
database, installed PostGIS, redis-server and the project's installed backend.
"""

import asyncio
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy.engine import make_url

from alert_failure_smoke import free_port, stop

ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, description, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, URLError):
            pass  # Only bounded startup/reconnection failures are tolerated.
        time.sleep(0.5)
    raise RuntimeError("Timed out: " + description)


def main():
    settings = {**dotenv_values(ROOT / "backend/.env"), **os.environ}
    source = make_url(settings.get("TEST_DATABASE_URL", ""))
    if (source.drivername != "postgresql+asyncpg" or source.query
            or source.host not in {"localhost", "127.0.0.1", "::1"}
            or not source.database or not source.database.endswith("_test")):
        raise ValueError("TEST_DATABASE_URL must identify a local _test database")
    database_name = "emergency_queue_" + uuid4().hex + "_test"
    target = source.set(database=database_name)
    admin_url = source.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)

    async def database_action(create):
        connection = await asyncpg.connect(admin_url, timeout=5)
        try:
            # Name is generated above, never taken from an input or existing DB.
            command = "CREATE DATABASE" if create else "DROP DATABASE"
            suffix = "" if create else " WITH (FORCE)"
            await connection.execute(f'{command} "{database_name}"{suffix}')
        finally:
            await connection.close()

    redis_server = shutil.which("redis-server")
    if not redis_server:
        raise RuntimeError("redis-server is required")
    redis_port, api_port = free_port(), free_port()
    metrics_token = secrets.token_urlsafe(32)
    signing = {}
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            expected = "sha256=" + hmac.new(
                signing["secret"].encode(),
                self.headers["X-Emergency-Timestamp"].encode() + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            valid = hmac.compare_digest(expected, self.headers["X-Emergency-Signature"])
            received.append(valid)
            self.send_response(200 if valid else 400)
            self.end_headers()

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    Thread(target=receiver.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{receiver.server_port}"
    environment = {
        **os.environ, "APP_ENV": "test",
        "DATABASE_URL": target.render_as_string(hide_password=False),
        "REDIS_URL": f"redis://127.0.0.1:{redis_port}/0",
        "CELERY_BROKER_URL": f"redis://127.0.0.1:{redis_port}/0",
        "CELERY_RESULT_BACKEND": f"redis://127.0.0.1:{redis_port}/1",
        "JWT_SECRET": secrets.token_urlsafe(48), "METRICS_TOKEN": metrics_token,
        "WEBHOOK_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "WEBHOOK_ALLOWED_LOOPBACK_ORIGINS": origin,
        "ALLOWED_HOSTS": "localhost,127.0.0.1", "ALLOWED_ORIGINS": "http://localhost:5173",
        "DB_SSL_CA_FILE": "", "LLM_PROVIDER": "fake", "OPENAI_API_KEY": "",
    }
    base = f"http://127.0.0.1:{api_port}"
    token = None
    processes = []
    created = False

    def request(path, payload=None):
        credential = metrics_token if path == "/metrics" else token
        headers = {"Authorization": "Bearer " + credential} if credential else {}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        with urlopen(Request(base + path, headers=headers, data=data), timeout=5) as response:
            body = response.read().decode()
            return body if path == "/metrics" else json.loads(body)

    def metrics():
        return {sample.name: sample.value
                for family in text_string_to_metric_families(request("/metrics"))
                for sample in family.samples if not sample.labels}

    with tempfile.TemporaryDirectory(prefix="emergency-queue-smoke-") as directory:
        with (Path(directory) / "processes.log").open("w") as log:
            def launch(arguments):
                process = subprocess.Popen(arguments, cwd=ROOT / "backend", env=environment,
                                           stdout=log, stderr=log)
                processes.append(process)
                return process

            try:
                asyncio.run(database_action(True))
                created = True
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                               cwd=ROOT / "backend", env=environment, stdout=log, stderr=log,
                               check=True, timeout=60)
                launch([redis_server, "--bind", "127.0.0.1", "--port", str(redis_port),
                        "--save", "", "--appendonly", "no", "--dir", directory])
                launch([sys.executable, "-c",
                        "import os, uvicorn; from app.main import create_app; "
                        "uvicorn.run(create_app(database_url=os.environ['DATABASE_URL'], "
                        "simulation_enabled=False, realtime_enabled=True), host='127.0.0.1', "
                        f"port={api_port}, access_log=False, timeout_graceful_shutdown=5)"])
                wait_for(lambda: request("/health/ready"), "API readiness")
                publisher = launch([sys.executable, "-m", "app.workers.publisher"])
                wait_for(lambda: metrics()["outbox_publisher_heartbeat_present"] == 1, "publisher heartbeat")
                token = request("/api/auth/register", {
                    "organization_name": "Fictional queue rehearsal", "display_name": "Rehearsal admin",
                    "email": uuid4().hex + "@example.com", "password": secrets.token_urlsafe(32),
                })["access_token"]
                stop(publisher)
                subscription = request("/api/webhooks", {
                    "name": "Isolated test receiver", "url": origin + "/events",
                    "event_types": ["incident.created"],
                })
                signing["secret"] = subscription["signing_secret"]
                incident = request("/api/incidents", {
                    "type": "medical", "severity": "low", "description": "Fictional monitoring exercise",
                    "latitude": 1.3, "longitude": 103.85,
                })
                job_id = request(f"/api/incidents/{incident['id']}/reports", {})["job_id"]
                wait_for(lambda: metrics()["outbox_publisher_heartbeat_age_seconds"] > 31,
                         "stopped publisher becomes stale")
                stalled = metrics()
                assert stalled["publisher_redis_available"] == 1
                assert stalled["outbox_pending_events"] == 1
                assert stalled["outbox_oldest_pending_age_seconds"] >= 3
                assert stalled["background_job_oldest_pending_age_seconds"] >= 3
                assert request("/api/jobs/" + job_id)["status"] == "QUEUED"
                print("PASS stopped publisher: heartbeat >31s with Redis healthy; durable outbox and job ages increase.")
                publisher = launch([sys.executable, "-m", "app.workers.publisher"])
                wait_for(lambda: metrics()["outbox_publisher_heartbeat_age_seconds"] < 5, "publisher recovery")
                wait_for(lambda: metrics()["outbox_pending_events"] == 0, "outbox materialization")
                assert metrics()["background_job_oldest_pending_age_seconds"] >= stalled["background_job_oldest_pending_age_seconds"]
                launch([sys.executable, "-m", "celery", "-A", "app.workers.celery_app:celery_app",
                        "worker", "--pool=solo", "--loglevel=INFO", "--without-gossip", "--without-mingle"])
                wait_for(lambda: request("/api/jobs/" + job_id)["status"] == "SUCCEEDED", "real worker report")
                wait_for(lambda: any(row["status"] == "DELIVERED" for row in request("/api/webhook-deliveries")),
                         "real signed webhook delivery")
                assert received and all(received)
                recovered = metrics()
                assert recovered["background_job_oldest_pending_age_seconds"] == 0
                assert recovered["outbox_oldest_pending_age_seconds"] == 0
                print("PASS real Redis/Celery recovery: report SUCCEEDED, signed HTTP delivery DELIVERED, both backlog ages zero.")
            except Exception:
                # Never print process logs: they may include connection diagnostics.
                print("FAIL isolated queue rehearsal; child exit codes:", [p.poll() for p in processes])
                raise
            finally:
                for process in reversed(processes):
                    stop(process)
                receiver.shutdown()
                receiver.server_close()
                if created:
                    asyncio.run(database_action(False))
                    print("Cleanup: task-created database removed and all task children stopped.")


if __name__ == "__main__":
    main()
