"""Offline deployment safety checks: no AWS credentials or resources required."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_script(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[2] / "scripts" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration():
    return {
        "region": "test-region",
        "cluster": "test",
        "subnets": ["private"],
        "security_groups": ["tasks"],
        "api_desired_count": 2,
        "task_families": {
            key: key for key in ["api", "worker", "publisher", "frontend", "migrate"]
        },
        "services": {key: key for key in ["api", "worker", "publisher", "frontend"]},
        "ecr": {"backend": "example/backend", "frontend": "example/frontend"},
    }


def test_deployment_rejects_mutable_image_before_any_aws_call(monkeypatch):
    module = load_script("aws_deploy")
    monkeypatch.setattr(
        module, "aws", lambda *args: pytest.fail("Unexpected AWS operation")
    )
    with pytest.raises(module.DeploymentError):
        module.deploy(configuration(), "latest")


def test_failed_migration_prevents_every_service_update(monkeypatch):
    module = load_script("aws_deploy")
    calls = []

    def fake(config, *args):
        calls.append(args)
        if args[1] == "describe-task-definition":
            return {
                "taskDefinition": {
                    "family": "migrate",
                    "containerDefinitions": [{"name": "migrate"}],
                }
            }
        if args[1] == "register-task-definition":
            return {"taskDefinition": {"taskDefinitionArn": "new-migration"}}
        if args[1] == "run-task":
            return {"tasks": [{"taskArn": "migration-task"}]}
        if args[1] == "wait":
            return {}
        if args[1] == "describe-tasks":
            return {"tasks": [{"containers": [{"exitCode": 1}]}]}
        pytest.fail("Unexpected service operation")

    monkeypatch.setattr(module, "aws", fake)
    with pytest.raises(module.DeploymentError, match="One-off task failed"):
        module.deploy(configuration(), "sha-" + "a" * 40)
    assert not any("update-service" in call for call in calls)


def test_task_registration_preserves_runtime_secrets_without_response_metadata():
    module = load_script("aws_deploy")
    original = {
        "family": "api",
        "revision": 3,
        "status": "ACTIVE",
        "taskDefinitionArn": "old",
        "containerDefinitions": [
            {
                "name": "api",
                "image": "old",
                "secrets": [{"name": "JWT_SECRET", "valueFrom": "secret-arn"}],
            }
        ],
    }
    result = module.task_payload(original, "new-image")
    assert "revision" not in result and "taskDefinitionArn" not in result
    assert result["containerDefinitions"][0]["image"] == "new-image"
    assert (
        result["containerDefinitions"][0]["secrets"]
        == original["containerDefinitions"][0]["secrets"]
    )
    assert original["containerDefinitions"][0]["image"] == "old"


def test_secret_initializer_refuses_existing_values(monkeypatch):
    module = load_script("aws_init_secrets")
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        return {"VersionIdsToStages": {"version": ["AWSCURRENT"]}}

    monkeypatch.setattr(module, "call", fake)
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        module.initialize({"runtime_secret_arn": "test-secret"})
    assert len(calls) == 1


def test_rds_bundle_loads_as_ca_certificates():
    import ssl

    path = Path(__file__).resolve().parents[1] / "app/db/rds-ca-bundle.pem"
    context = ssl.create_default_context(cafile=str(path))
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.cert_store_stats()["x509_ca"] > 0


@pytest.mark.parametrize("rollback", [False, True])
def test_rollout_waits_for_migration_and_detects_stable_rollback(monkeypatch, rollback):
    import json

    module = load_script("aws_deploy")
    calls = []

    def fake(config, *args):
        calls.append(args)
        operation = args[1]
        if operation == "describe-task-definition":
            name = args[-1]
            return {
                "taskDefinition": {
                    "family": name,
                    "containerDefinitions": [{"name": name}],
                }
            }
        if operation == "register-task-definition":
            payload = json.loads(args[-1])
            return {"taskDefinition": {"taskDefinitionArn": "new-" + payload["family"]}}
        if operation == "run-task":
            return {"tasks": [{"taskArn": "migration-task"}]}
        if operation == "describe-tasks":
            return {"tasks": [{"containers": [{"exitCode": 0}]}]}
        if operation == "describe-services":
            return {
                "services": [
                    {
                        "serviceName": name,
                        "taskDefinition": ("old-" if rollback else "new-") + name,
                        "runningCount": 2 if name == "api" else 1,
                        "desiredCount": 2 if name == "api" else 1,
                    }
                    for name in config["services"]
                ]
            }
        if operation in ["wait", "update-service"]:
            return {}
        pytest.fail("Unexpected AWS operation")

    monkeypatch.setattr(module, "aws", fake)
    if rollback:
        with pytest.raises(module.DeploymentError, match="rolled back"):
            module.deploy(configuration(), "sha-" + "a" * 40)
    else:
        module.deploy(configuration(), "sha-" + "a" * 40)
    migration_verified = next(
        i for i, call in enumerate(calls) if call[1] == "describe-tasks"
    )
    updates = [i for i, call in enumerate(calls) if call[1] == "update-service"]
    assert len(updates) == 4 and min(updates) > migration_verified


def test_generated_runtime_secret_uses_verified_tls_and_stdin(monkeypatch, capsys):
    import json

    module = load_script("aws_init_secrets")
    stored = {}

    def fake(config, *args, payload=None):
        if args[1] == "describe-secret":
            return {}
        if args[1] == "get-secret-value":
            return {"SecretString": "redis-private-password"}
        assert args == (
            "secretsmanager",
            "put-secret-value",
            "--cli-input-json",
            "file:///dev/stdin",
        )
        stored.update(json.loads(payload["SecretString"]))
        return {}

    monkeypatch.setattr(module, "call", fake)
    module.initialize(
        {
            "runtime_secret_arn": "runtime",
            "redis_secret_arn": "redis",
            "redis_host": "cache.example.com",
            "database_host": "db.example.com",
        }
    )
    assert stored["DATABASE_URL"].endswith("?ssl=verify-full")
    assert stored["MIGRATION_DATABASE_URL"].startswith(
        "postgresql+asyncpg://emergency_migrator:"
    )
    assert stored["REDIS_URL"].startswith("rediss://") and stored["REDIS_URL"].endswith(
        "ssl_cert_reqs=required"
    )
    assert len(stored["JWT_SECRET"]) >= 32 and stored["OPENAI_API_KEY"] == ""
    assert "redis-private-password" not in capsys.readouterr().out


def test_rds_bootstrap_requires_verified_ca_and_creates_limited_role(monkeypatch):
    import asyncio
    import ssl
    from contextlib import asynccontextmanager
    import app.scripts.bootstrap_database as bootstrap

    captured = {}

    class Connection:
        @asynccontextmanager
        async def transaction(self):
            yield

        async def fetchval(self, query, *args):
            if "pg_roles" in query:
                return None
            if "quote_literal" in query:
                captured.setdefault("raw_password_parameters", []).append(args[0])
                return "'" + args[0] + "'"

        async def fetch(self, _query):
            return []

        async def execute(self, query):
            captured.setdefault("sql", []).append(query)

        async def close(self):
            captured["closed"] = True

    async def connect(**kwargs):
        captured.update(kwargs)
        return Connection()

    monkeypatch.setattr(bootstrap.asyncpg, "connect", connect)
    ca = Path(__file__).resolve().parents[1] / "app/db/rds-ca-bundle.pem"
    monkeypatch.setenv("DB_SSL_CA_FILE", str(ca))
    monkeypatch.setenv("RDS_USERNAME", "managed-master")
    monkeypatch.setenv("RDS_PASSWORD", "a generated master password")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://emergency_app:generated-password@db.example.com/emergency_platform?ssl=verify-full",
    )
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql+asyncpg://emergency_migrator:migration-password@db.example.com/emergency_platform?ssl=verify-full",
    )
    asyncio.run(bootstrap.initialize())
    assert captured["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert captured["ssl"].check_hostname
    assert captured["ssl"].get_ca_certs()
    roles = [sql for sql in captured["sql"] if sql.startswith("CREATE ROLE")]
    assert len(roles) == 2
    assert all("NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql for sql in roles)
    assert any(sql.endswith("PASSWORD 'generated-password'") for sql in roles)
    assert any(sql.endswith("PASSWORD 'migration-password'") for sql in roles)
    assert set(captured["raw_password_parameters"]) == {
        "generated-password",
        "migration-password",
    }
    assert any("REVOKE CREATE" in sql and "emergency_app" in sql for sql in captured["sql"])
    assert any("ALTER DEFAULT PRIVILEGES" in sql for sql in captured["sql"])
    assert captured["closed"]
