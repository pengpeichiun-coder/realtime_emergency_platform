"""Initialize an EMPTY runtime secret after explicit cost approval; never print values.
Run once as an operator, before the dedicated database bootstrap task. No rotation.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
from urllib.parse import quote


def call(config, *args, payload=None):
    result = subprocess.run(
        ["aws", "--region", config["region"], *args, "--output", "json"],
        input=json.dumps(payload) if payload else None,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError("AWS secret operation failed")
    return json.loads(result.stdout)


def initialize(config):
    metadata = call(
        config,
        "secretsmanager",
        "describe-secret",
        "--secret-id",
        config["runtime_secret_arn"],
    )
    if metadata.get("VersionIdsToStages"):
        raise RuntimeError(
            "Runtime secret already initialized; refusing to overwrite credentials"
        )
    redis_password = call(
        config,
        "secretsmanager",
        "get-secret-value",
        "--secret-id",
        config["redis_secret_arn"],
    )["SecretString"]
    database_password = secrets.token_urlsafe(40)
    migration_password = secrets.token_urlsafe(40)
    redis_base = (
        "rediss://:"
        + quote(redis_password, safe="")
        + "@"
        + config["redis_host"]
        + ":6379/"
    )
    values = {
        "DATABASE_URL": "postgresql+asyncpg://emergency_app:"
        + quote(database_password, safe="")
        + "@"
        + config["database_host"]
        + "/emergency_platform?ssl=verify-full",
        "MIGRATION_DATABASE_URL": "postgresql+asyncpg://emergency_migrator:"
        + quote(migration_password, safe="")
        + "@"
        + config["database_host"]
        + "/emergency_platform?ssl=verify-full",
        "REDIS_URL": redis_base + "0?ssl_cert_reqs=required",
        "CELERY_BROKER_URL": redis_base + "0?ssl_cert_reqs=required",
        "CELERY_RESULT_BACKEND": redis_base + "1?ssl_cert_reqs=required",
        "JWT_SECRET": secrets.token_urlsafe(48),
        "WEBHOOK_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "METRICS_TOKEN": secrets.token_urlsafe(32),
        "OPENAI_API_KEY": "",
    }
    # stdin prevents secret values from appearing in process command arguments.
    call(
        config,
        "secretsmanager",
        "put-secret-value",
        "--cli-input-json",
        "file:///dev/stdin",
        payload={
            "SecretId": config["runtime_secret_arn"],
            "SecretString": json.dumps(values),
        },
    )
    print(
        "Runtime secret initialized; no values printed. Database role bootstrap is next."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--approve-billable", action="store_true", required=True)
    args = parser.parse_args()
    try:
        initialize(json.loads(args.config.read_text()))
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(
            str(exc) if isinstance(exc, RuntimeError) else "AWS operation timed out"
        ) from None


if __name__ == "__main__":
    main()
