"""Exercise liveness, readiness, command failure, and recovery during a DB outage."""

import asyncio
import os
import secrets
import subprocess
import time
from uuid import uuid4

import aiohttp


BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8002").rstrip("/")


def compose(*args):
    subprocess.run(["docker", "compose", *args], check=True, capture_output=True)


async def status(client, path, expected, token=None, body=None, method="GET"):
    headers = {"Authorization": "Bearer " + token} if token else {}
    async with client.request(
        method, BASE + path, headers=headers, json=body, timeout=8
    ) as response:
        if response.status not in expected:
            raise AssertionError(f"{method} {path}: unexpected HTTP {response.status}")
        return response.status, await response.json() if response.status < 400 else None


async def main():
    async with aiohttp.ClientSession() as client:
        await status(client, "/health/ready", {200})
        _, registered = await status(
            client,
            "/api/auth/register",
            {201},
            method="POST",
            body={
                "organization_name": "PostgreSQL recovery " + uuid4().hex,
                "display_name": "Recovery operator",
                "email": uuid4().hex + "@example.com",
                "password": secrets.token_urlsafe(24),
            },
        )
        token = registered["access_token"]
        await status(client, "/api/demo/reset", {200}, token, method="POST")
        marker = "DO_NOT_COMMIT_DB_OUTAGE_" + uuid4().hex
        stopped = False
        try:
            await asyncio.to_thread(compose, "stop", "postgres")
            stopped = True
            live, _ = await status(client, "/health/live", {200})
            ready, _ = await status(client, "/health/ready", {503})
            command, _ = await status(
                client,
                "/api/incidents",
                {500, 503},
                token,
                {
                    "type": "medical",
                    "severity": "low",
                    "description": marker,
                    "latitude": 1.29,
                    "longitude": 103.85,
                },
                "POST",
            )
            if live != 200 or ready != 503 or command < 500:
                raise AssertionError("PostgreSQL outage returned a misleading success.")
        finally:
            if stopped:
                await asyncio.to_thread(compose, "start", "postgres")

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                async with client.get(BASE + "/health/ready", timeout=3) as response:
                    if response.status == 200:
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(1)
        else:
            raise AssertionError("API readiness did not recover after PostgreSQL restart.")

        _, recovered = await status(client, "/api/state", {200}, token)
        descriptions = {item["description"] for item in recovered["incidents"]}
        if marker in descriptions or len(recovered["incidents"]) < 6:
            raise AssertionError("The failed command was committed or stored state was lost.")
        print(
            "PASS PostgreSQL outage: liveness stayed 200, readiness became 503, "
            "command failed with 5xx, then readiness and durable REST state recovered."
        )


if __name__ == "__main__":
    asyncio.run(main())
