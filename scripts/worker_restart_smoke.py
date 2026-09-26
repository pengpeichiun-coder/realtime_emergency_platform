"""Queue a report with Celery stopped, restart the worker, and verify completion."""

import json
import os
import secrets
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:18082").rstrip("/")


def request(method: str, path: str, body=None, token=None, expected=200):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode() if body is not None else None
    call = Request(BASE_URL + path, data=payload, headers=headers, method=method)
    try:
        response = urlopen(call, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        data = response.read()
        if response.status != expected:
            raise AssertionError(f"{method} {path} returned HTTP {response.status}, expected {expected}")
        return json.loads(data) if data else None


def compose(*args):
    subprocess.run(["docker", "compose", *args], check=True, capture_output=True, text=True)


def main():
    registered = request(
        "POST",
        "/api/auth/register",
        {
            "organization_name": "Worker restart " + uuid4().hex,
            "display_name": "Fictional rehearsal administrator",
            "email": uuid4().hex + "@example.com",
            "password": secrets.token_urlsafe(24),
        },
        expected=201,
    )
    token = registered["access_token"]
    incident = request(
        "POST",
        "/api/incidents",
        {
            "type": "medical",
            "severity": "low",
            "description": "Fictional worker-restart deployment rehearsal.",
            "latitude": 1.29,
            "longitude": 103.85,
        },
        token,
        expected=201,
    )

    stopped = False
    try:
        compose("stop", "worker")
        stopped = True
        queued = request(
            "POST", f"/api/incidents/{incident['id']}/reports", token=token, expected=202
        )
        job_id = queued["job_id"]
        time.sleep(1)
        pending = request("GET", f"/api/jobs/{job_id}", token=token)
        if pending["status"] != "QUEUED" or pending["attempt_count"] != 0:
            raise AssertionError("job changed state while the only worker was stopped")

        compose("start", "worker")
        stopped = False
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = request("GET", f"/api/jobs/{job_id}", token=token)
            if job["status"] == "FAILED":
                raise AssertionError(f"report job failed: {job['error_code']}")
            if job["status"] == "SUCCEEDED":
                if not job["result_payload"] or job["attempt_count"] != 1:
                    raise AssertionError("job result or idempotent attempt count was incorrect")
                print("PASS queued report survived a Celery worker restart and completed once.")
                return
            time.sleep(0.5)
        raise TimeoutError("report job did not complete after the worker restarted")
    finally:
        if stopped:
            compose("start", "worker")


if __name__ == "__main__":
    main()
