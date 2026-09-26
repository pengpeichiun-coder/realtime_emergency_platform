"""Real local Redis outage/recovery against an isolated HTTP API and test DB.

Run with backend/.venv/bin/python. Requires redis-server and a migrated local
TEST_DATABASE_URL ending in _test. No records, services or .env files are changed.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import dotenv_values
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main():
    settings = {**dotenv_values(ROOT / "backend/.env"), **os.environ}
    target = settings.get("TEST_DATABASE_URL", "")
    url = make_url(target)
    local = {"localhost", "127.0.0.1", "::1"}
    if (url.drivername != "postgresql+asyncpg" or url.host not in local or url.query
            or not url.database or not url.database.endswith("_test")):
        raise ValueError("Use a dedicated local PostgreSQL _test database")
    if settings.get("DATABASE_URL"):
        development = make_url(settings["DATABASE_URL"])
        if (development.host in local | {None} and development.database == url.database
                and (development.port or 5432) == (url.port or 5432)):
            raise ValueError("Test and development databases must be different")
    redis_server = shutil.which("redis-server")
    if not redis_server:
        raise RuntimeError("redis-server is required")
    redis_port, api_port = free_port(), free_port()
    token = secrets.token_urlsafe(32)
    environment = {
        **os.environ, "APP_ENV": "test", "DATABASE_URL": target,
        "REDIS_URL": f"redis://127.0.0.1:{redis_port}/0",
        "JWT_SECRET": secrets.token_urlsafe(32), "METRICS_TOKEN": token,
        "ALLOWED_HOSTS": "localhost,127.0.0.1",
        "ALLOWED_ORIGINS": "http://localhost:5173", "DB_SSL_CA_FILE": "",
    }
    base = f"http://127.0.0.1:{api_port}"
    api = redis = None
    with tempfile.TemporaryDirectory(prefix="emergency-alert-smoke-") as directory:
        with (Path(directory) / "processes.log").open("w") as log:
            def start_redis():
                return subprocess.Popen([
                    redis_server, "--bind", "127.0.0.1", "--port", str(redis_port),
                    "--save", "", "--appendonly", "no", "--dir", directory,
                ], stdout=log, stderr=log)

            def request(path, payload=None):
                headers = {"Authorization": "Bearer " + token} if path == "/metrics" else {}
                data = None
                if payload is not None:
                    headers["Content-Type"] = "application/json"
                    data = json.dumps(payload).encode()
                try:
                    with urlopen(Request(base + path, headers=headers, data=data), timeout=5) as response:
                        return response.status, response.read().decode()
                except HTTPError as error:
                    return error.code, error.read().decode()

            def wait_ready():
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    if api.poll() is not None or redis.poll() is not None:
                        raise RuntimeError("An isolated smoke process exited early")
                    try:
                        if request("/health/ready")[0] == 200:
                            return
                    except (OSError, URLError):
                        pass  # Startup/reconnection can precede listening; bounded above.
                    time.sleep(0.25)
                raise RuntimeError("Isolated API readiness did not recover")

            def metric(name, labels=None):
                status, body = request("/metrics")
                assert status == 200, "Metrics must remain readable during Redis outage"
                return sum(
                    sample.value for family in text_string_to_metric_families(body)
                    for sample in family.samples if sample.name == name
                    and all(sample.labels.get(key) == value for key, value in (labels or {}).items())
                )

            bad_login = {"email": secrets.token_hex(12) + "@example.com", "password": secrets.token_urlsafe(24)}
            try:
                redis = start_redis()
                api = subprocess.Popen([
                    sys.executable, "-c",
                    "import os, uvicorn; from app.main import create_app; "
                    "uvicorn.run(create_app(database_url=os.environ['DATABASE_URL'], "
                    "simulation_enabled=False, realtime_enabled=True), host='127.0.0.1', "
                    f"port={api_port}, access_log=False, timeout_graceful_shutdown=5)",
                ], cwd=ROOT / "backend", env=environment, stdout=log, stderr=log)
                wait_ready()
                assert request("/api/auth/login", bad_login)[0] == 401
                before = metric("auth_rate_limit_fallback_total")
                stop(redis)  # Only the Redis child created by this command.
                assert request("/health/ready")[0] == 503
                for _ in range(3):
                    assert request("/api/auth/login", bad_login)[0] == 401
                after = metric("auth_rate_limit_fallback_total")
                assert after - before == 3
                assert metric("http_requests_total", {"route": "/health/ready", "status": "503"}) >= 1
                redis = start_redis()
                wait_ready()
                assert request("/api/auth/login", bad_login)[0] == 401
                assert metric("auth_rate_limit_fallback_total") == after
                print("PASS real isolated Redis outage: ready 200 -> 503 -> 200; "
                      "fallback counter +3, then stable after recovery; metrics stayed 200.")
            finally:
                stop(api)
                stop(redis)


if __name__ == "__main__":
    main()
