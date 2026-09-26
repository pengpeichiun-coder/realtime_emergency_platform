"""Kill real Celery task children while a report job is RUNNING.

Run only against this repository's isolated local Compose project. The held table
lock is released in a finally block and does not change database contents.
"""

import asyncio
import json
import os
import secrets
import subprocess
from uuid import uuid4

import aiohttp


BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8080").rstrip("/")


def compose(*args):
    subprocess.run(
        ["docker", "compose", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )


def worker_child_pids():
    inspect_code = (
        "import json; from app.workers.celery_app import celery_app; "
        "print(json.dumps(celery_app.control.inspect(timeout=5).stats() or {}))"
    )
    stats_json = subprocess.check_output(
        ["docker", "compose", "exec", "-T", "worker", "python", "-c", inspect_code],
        text=True,
        timeout=15,
    )
    stats = json.loads(stats_json)
    return sorted(
        {
            int(pid)
            for worker in stats.values()
            for pid in worker.get("pool", {}).get("processes", [])
        }
    )


async def main():
    async with aiohttp.ClientSession() as client:
        async def request(method, path, *, token=None, body=None, expected=200):
            headers = {"Authorization": "Bearer " + token} if token else {}
            async with client.request(
                method, BASE + path, json=body, headers=headers, timeout=10
            ) as response:
                if response.status != expected:
                    raise AssertionError(f"{method} {path}: HTTP {response.status}")
                return await response.json()

        registered = await request(
            "POST",
            "/api/auth/register",
            body={
                "organization_name": "Worker recovery " + uuid4().hex,
                "display_name": "Recovery operator",
                "email": uuid4().hex + "@example.com",
                "password": secrets.token_urlsafe(24),
            },
            expected=201,
        )
        token = registered["access_token"]
        await request("POST", "/api/demo/reset", token=token)

        lock = None
        locked = False
        try:
            # Queue one durable job without letting either process claim it yet.
            await asyncio.to_thread(compose, "stop", "worker")
            await asyncio.to_thread(compose, "stop", "publisher")
            created = await request(
                "POST",
                "/api/incidents/INC-001/reports",
                token=token,
                expected=202,
            )
            job_id = created["job_id"]

            lock = subprocess.Popen(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-X",
                    "-qAt",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "emergency",
                    "-d",
                    "emergency_platform",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            lock.stdin.write(
                "BEGIN;\nLOCK TABLE public.incidents IN ACCESS EXCLUSIVE MODE;"
                "\nSELECT 'PHASE16_TABLE_LOCKED';\n"
            )
            lock.stdin.flush()
            marker = await asyncio.wait_for(
                asyncio.to_thread(lock.stdout.readline), timeout=10
            )
            if marker.strip() != "PHASE16_TABLE_LOCKED":
                raise RuntimeError("Could not establish the controlled task lock.")
            locked = True
            await asyncio.to_thread(compose, "up", "-d", "--no-deps", "worker")
            child_pids = []
            for _ in range(30):
                child_pids = await asyncio.to_thread(worker_child_pids)
                if child_pids:
                    break
                await asyncio.sleep(0.25)
            if not child_pids:
                raise AssertionError("Celery did not report any ready task children.")
            await asyncio.to_thread(compose, "up", "-d", "--no-deps", "publisher")
            for _ in range(120):
                job = await request("GET", "/api/jobs/" + job_id, token=token)
                if job["status"] == "RUNNING":
                    break
                await asyncio.sleep(0.25)
            else:
                raise AssertionError("Worker did not enter the controlled RUNNING state.")

            # All prefork children were recorded while idle. Killing the pool at
            # this point guarantees the child holding this task is also lost.
            await asyncio.to_thread(
                compose,
                "exec",
                "-T",
                "worker",
                "python",
                "-c",
                "import os, signal; "
                f"[os.kill(pid, signal.SIGKILL) for pid in {child_pids!r}]",
            )
            lock.stdin.write("COMMIT;\n\\q\n")
            lock.stdin.flush()
            locked = False
            lock.stdin.close()
            await asyncio.to_thread(lock.wait, 10)
            await asyncio.to_thread(
                compose, "up", "-d", "--no-deps", "worker"
            )

            for _ in range(120):
                job = await request("GET", "/api/jobs/" + job_id, token=token)
                if job["status"] == "SUCCEEDED":
                    break
                if job["status"] == "FAILED":
                    raise AssertionError(job["error_code"])
                await asyncio.sleep(0.5)
            else:
                raise AssertionError("Redelivered report did not finish.")
            if job["attempt_count"] < 2 or not job["result_payload"]:
                raise AssertionError("The worker did not safely redeliver the task.")

            pong = await asyncio.to_thread(
                subprocess.check_output,
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "worker",
                    "celery",
                    "-A",
                    "app.workers.celery_app:celery_app",
                    "inspect",
                    "ping",
                    "--timeout=5",
                ],
                stderr=subprocess.STDOUT,
                text=True,
            )
            if "pong" not in pong.lower():
                raise AssertionError("Celery did not acknowledge worker recovery.")
            print(
                "PASS Celery task-child SIGKILL while a durable job was RUNNING; Redis redelivered "
                f"{job_id} after task children {child_pids} died, PostgreSQL fenced duplicate effects, "
                f"and attempt {job['attempt_count']} succeeded."
            )
        finally:
            if locked and lock is not None and lock.poll() is None:
                lock.stdin.write("ROLLBACK;\n\\q\n")
                lock.stdin.flush()
                lock.stdin.close()
                await asyncio.to_thread(lock.wait, 10)
            if lock is not None and lock.poll() is None:
                lock.terminate()
                await asyncio.to_thread(lock.wait, 5)
            await asyncio.to_thread(
                compose, "up", "-d", "--no-deps", "worker", "publisher"
            )


if __name__ == "__main__":
    asyncio.run(main())
