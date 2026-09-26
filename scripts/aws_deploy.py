"""Explicitly approved ECS deployment: one migration must succeed before rollout.

No Terraform apply and no secret values. All AWS writes require the approval flag.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


class DeploymentError(RuntimeError):
    pass


def aws(config, *args):
    result = subprocess.run(
        ["aws", "--region", config["region"], *args, "--output", "json"],
        capture_output=True,
        text=True,
        timeout=660,
    )
    if result.returncode:
        raise DeploymentError("AWS command failed: " + " ".join(args[:2]))
    return json.loads(result.stdout) if result.stdout.strip() else {}


def task_payload(description, image):
    # Only fields accepted by RegisterTaskDefinition, not response-only metadata.
    allowed = {
        "family",
        "taskRoleArn",
        "executionRoleArn",
        "networkMode",
        "containerDefinitions",
        "volumes",
        "placementConstraints",
        "requiresCompatibilities",
        "cpu",
        "memory",
        "pidMode",
        "ipcMode",
        "proxyConfiguration",
        "inferenceAccelerators",
        "ephemeralStorage",
        "runtimePlatform",
    }
    payload = {key: value for key, value in description.items() if key in allowed}
    payload = json.loads(json.dumps(payload))
    if len(payload["containerDefinitions"]) != 1:
        raise DeploymentError("Expected one application container per task")
    payload["containerDefinitions"][0]["image"] = image
    return payload


def register(config, component, tag):
    source = aws(
        config,
        "ecs",
        "describe-task-definition",
        "--task-definition",
        config["task_families"][component],
    )["taskDefinition"]
    image = (
        config["ecr"]["frontend" if component == "frontend" else "backend"] + ":" + tag
    )
    response = aws(
        config,
        "ecs",
        "register-task-definition",
        "--cli-input-json",
        json.dumps(task_payload(source, image)),
    )
    return response["taskDefinition"]["taskDefinitionArn"]


def run_one_off(config, definition):
    network = {
        "awsvpcConfiguration": {
            "subnets": config["subnets"],
            "securityGroups": config["security_groups"],
            "assignPublicIp": "DISABLED",
        }
    }
    response = aws(
        config,
        "ecs",
        "run-task",
        "--cluster",
        config["cluster"],
        "--task-definition",
        definition,
        "--launch-type",
        "FARGATE",
        "--platform-version",
        "1.4.0",
        "--count",
        "1",
        "--network-configuration",
        json.dumps(network),
    )
    if response.get("failures") or len(response.get("tasks", [])) != 1:
        raise DeploymentError(
            "One-off task placement failed; services were not updated"
        )
    identifier = response["tasks"][0]["taskArn"]
    aws(
        config,
        "ecs",
        "wait",
        "tasks-stopped",
        "--cluster",
        config["cluster"],
        "--tasks",
        identifier,
    )
    tasks = aws(
        config,
        "ecs",
        "describe-tasks",
        "--cluster",
        config["cluster"],
        "--tasks",
        identifier,
    )
    if tasks.get("failures") or not tasks.get("tasks"):
        raise DeploymentError(
            "One-off task status unavailable; services were not updated"
        )
    containers = tasks["tasks"][0].get("containers", [])
    if len(containers) != 1 or containers[0].get("exitCode") != 0:
        raise DeploymentError(
            "One-off task failed; inspect CloudWatch. Services were not updated"
        )


def deploy(config, tag, bootstrap=False):
    if not re.fullmatch(r"sha-[a-f0-9]{40}", tag):
        raise DeploymentError(
            "An immutable sha-<40 lowercase hex characters> tag is required"
        )
    if bootstrap:
        # Requires an operator role: normal GitHub deploy role cannot run bootstrap.
        run_one_off(config, register(config, "bootstrap", tag))
        print("Initial role/extension bootstrap completed; no services were started.")
        return
    run_one_off(config, register(config, "migrate", tag))
    definitions = {
        name: register(config, name, tag)
        for name in ("api", "worker", "publisher", "frontend")
    }
    for name, definition in definitions.items():
        aws(
            config,
            "ecs",
            "update-service",
            "--cluster",
            config["cluster"],
            "--service",
            config["services"][name],
            "--task-definition",
            definition,
            "--desired-count",
            str(config["api_desired_count"] if name == "api" else 1),
        )
    aws(
        config,
        "ecs",
        "wait",
        "services-stable",
        "--cluster",
        config["cluster"],
        "--services",
        *config["services"].values(),
    )
    # A circuit-breaker rollback can also become stable; reject an old revision.
    status = aws(
        config,
        "ecs",
        "describe-services",
        "--cluster",
        config["cluster"],
        "--services",
        *config["services"].values(),
    )
    if status.get("failures") or len(status.get("services", [])) != 4:
        raise DeploymentError("Service status incomplete")
    for name, definition in definitions.items():
        service = next(
            (
                row
                for row in status["services"]
                if row["serviceName"] == config["services"][name]
            ),
            None,
        )
        if (
            not service
            or service["taskDefinition"] != definition
            or service["runningCount"] != service["desiredCount"]
        ):
            raise DeploymentError(
                "Deployment rolled back or did not reach desired count: " + name
            )
    print(
        "Migration succeeded; all four services reached the requested image revisions."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument("--bootstrap-database", action="store_true")
    parser.add_argument("--approve-billable", action="store_true", required=True)
    args = parser.parse_args()
    try:
        deploy(
            json.loads(args.config.read_text()), args.image_tag, args.bootstrap_database
        )
    except (DeploymentError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(
            str(exc) if isinstance(exc, DeploymentError) else "AWS operation timed out"
        ) from None


if __name__ == "__main__":
    main()
