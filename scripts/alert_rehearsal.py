"""Bounded real alert routing in a new Compose project; cleans up only that project."""

import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

from cryptography.fernet import Fernet
import yaml

from alert_failure_smoke import free_port

ROOT = Path(__file__).resolve().parents[1]
ALERT = "EmergencyOutboxPublisherUnavailable"


def get_json(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def wait_for(predicate, description, timeout=120, stable_for=0):
    deadline = time.monotonic() + timeout
    matched_since = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                matched_since = matched_since or time.monotonic()
                if time.monotonic() - matched_since >= stable_for:
                    return
            else:
                matched_since = None
        except (OSError, URLError):
            matched_since = None
        time.sleep(1)
    raise RuntimeError("Timed out: " + description)


def main():
    # The name cannot be overridden to target an existing project.
    project = "emergency-alert-rehearsal-" + uuid4().hex[:12]
    ports = {name: str(free_port()) for name in [
        "HTTP_PORT", "API2_PORT", "PROMETHEUS_PORT", "ALERTMANAGER_PORT", "ALERT_RECEIVER_PORT"
    ]}
    settings = {**ports, **{name: secrets.token_hex(24) for name in [
        "POSTGRES_PASSWORD", "RUNTIME_DATABASE_PASSWORD", "MIGRATION_DATABASE_PASSWORD", "JWT_SECRET", "METRICS_TOKEN"
    ]}, "WEBHOOK_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "APP_ENV": "development", "LLM_PROVIDER": "fake", "OPENAI_API_KEY": "",
        "ALLOWED_HOSTS": "127.0.0.1,localhost,testserver", "ALLOWED_ORIGINS": "http://localhost:5173"}
    # Explicit child environment wins over any real provider credential or
    # existing Compose project from the caller. Never print expanded config.
    environment = {**os.environ, **settings, "COMPOSE_PROJECT_NAME": project}
    prometheus = "http://127.0.0.1:" + ports["PROMETHEUS_PORT"]
    manager = "http://127.0.0.1:" + ports["ALERTMANAGER_PORT"]
    receiver = "http://127.0.0.1:" + ports["ALERT_RECEIVER_PORT"]

    # Colima shares the checkout, but may not share macOS's system temporary
    # directory. Bind-mounted configuration must live on that shared filesystem.
    staging_root = ROOT / ".local/monitoring-rehearsal"
    staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=staging_root) as directory:
        staging = Path(directory)
        env_file = staging / ".env"
        env_file.write_text("\n".join(f"{key}={value}" for key, value in settings.items()) + "\n")
        env_file.chmod(0o600)
        config = yaml.safe_load((ROOT / "monitoring/prometheus.yml").read_text())
        config["global"].update(scrape_interval="5s", evaluation_interval="2s")
        config["scrape_configs"][0]["scrape_timeout"] = "5s"
        config["rule_files"] = ["/etc/prometheus/alerts.rehearsal.yml"]
        shortened = staging / "prometheus.yml"
        shortened.write_text(yaml.safe_dump(config, sort_keys=False))
        override = staging / "compose.rehearsal.yaml"
        services = {"prometheus": {"volumes": [
            {"type": "bind", "source": str(shortened), "target": "/etc/prometheus/prometheus.yml",
             "read_only": True, "bind": {"create_host_path": False}},
            {"type": "bind", "source": str(ROOT / "monitoring/alerts.rehearsal.yml"),
             "target": "/etc/prometheus/alerts.rehearsal.yml", "read_only": True,
             "bind": {"create_host_path": False}},
        ]}}
        # Cold amd64 PostGIS/probe startup can be slow under Apple Silicon
        # emulation. Only this disposable rehearsal gets a larger process budget;
        # commands and actual readiness checks remain unchanged.
        for service in ("postgres", "api", "api2"):
            services[service] = {"healthcheck": {
                "start_period": "60s", "interval": "10s", "timeout": "15s", "retries": 30,
            }}
        override.write_text(yaml.safe_dump({"services": services}))
        command = shlex.split(os.environ.get("COMPOSE", "docker compose")) + [
            "--project-name", project, "--env-file", str(env_file),
            "-f", str(ROOT / "compose.yaml"), "-f", str(ROOT / "compose.monitoring.yaml"),
            "-f", str(override), "--profile", "monitoring",
        ]

        def redact(value):
            for key, secret in settings.items():
                if secret and key.endswith(("_PASSWORD", "_SECRET", "_TOKEN", "_KEY")):
                    value = value.replace(secret, "[redacted]")
            return value

        def compose(*arguments, timeout=180):
            result = subprocess.run(command + list(arguments), cwd=ROOT, env=environment,
                                    capture_output=True, text=True, timeout=timeout)
            if result.returncode:
                details = redact(result.stderr or result.stdout)
                raise RuntimeError(
                    f"Compose {' '.join(arguments[:2])} failed (exit {result.returncode}):\n{details[-5000:]}"
                )
            return result.stdout

        def query(expression):
            response = get_json(prometheus + "/api/v1/query?" + urlencode({"query": expression}))
            assert response["status"] == "success"
            return response["data"]["result"]

        def notified(status):
            return [event for event in get_json(receiver + "/events")
                    if event["alertname"] == ALERT and event["status"] == status]

        def healthy_publisher():
            targets = query('up{job="emergency-api"}')
            return (len(targets) == 2 and all(float(row["value"][1]) == 1 for row in targets)
                    and len(query('outbox_publisher_heartbeat_present{job="emergency-api"} == 1')) == 2
                    and len(query('outbox_publisher_heartbeat_age_seconds{job="emergency-api"} < 10')) == 2)

        started = False
        try:
            compose("config", "--quiet")
            print("Building current backend/PostGIS images for an isolated monitoring project.", flush=True)
            # Avoid concurrent Go compilation and Python dependency installation
            # exhausting smaller local Docker VMs.
            compose("build", "postgres", timeout=900)
            compose("build", "api", timeout=900)
            started = True
            print("Starting fresh PostGIS, Redis, two APIs, publisher and monitoring services (up to 10 minutes).", flush=True)
            compose("up", "-d", "publisher", "prometheus", timeout=600)
            wait_for(healthy_publisher, "two healthy scrape targets and fresh publisher heartbeat", stable_for=10)
            assert get_json(manager + "/api/v2/status")["cluster"]["status"] == "disabled"
            rules = get_json(prometheus + "/api/v1/rules")["data"]["groups"]
            assert any(rule["name"] == ALERT for group in rules for rule in group["rules"])
            # Ignore any startup notification only after the condition is known
            # inactive; the next firing must follow this controlled stop.
            wait_for(lambda: not query('ALERTS{alertname="' + ALERT + '",alertstate="firing"}'), "initial healthy alert state")
            before = len(notified("firing"))
            print("PASS Prometheus targets (2/2), bearer-protected metrics, rule loading and Alertmanager health.", flush=True)
            compose("stop", "publisher")
            wait_for(lambda: bool(query('ALERTS{alertname="' + ALERT + '",alertstate="firing"}')), "Prometheus firing")
            wait_for(lambda: any(alert["labels"].get("alertname") == ALERT
                                for alert in get_json(manager + "/api/v2/alerts")), "Alertmanager receives alert")
            wait_for(lambda: len(notified("firing")) > before, "receiver firing notification")
            firing = notified("firing")[-1]
            assert firing["labels"]["cluster"] == "local"
            assert "instance" not in firing["labels"] and firing["annotations"]["summary"]
            print("PASS real publisher stop -> Prometheus firing -> Alertmanager -> local receiver.", flush=True)
            resolved_before = len(notified("resolved"))
            # Restart only this stopped process, without re-running migrations
            # or disturbing other dependencies during the recovery measurement.
            compose("restart", "--no-deps", "publisher")
            wait_for(healthy_publisher, "heartbeat and both scrape targets recover", stable_for=10)
            wait_for(lambda: not query('ALERTS{alertname="' + ALERT + '",alertstate="firing"}'), "alert clears")
            wait_for(lambda: len(notified("resolved")) > resolved_before, "receiver resolved notification")
            print("PASS publisher recovery -> resolved notification; no external receiver or provider used.", flush=True)
        except Exception:
            if started:
                try:
                    diagnostic = compose("logs", "--no-color", "--tail", "20", timeout=30)
                    print(redact(diagnostic)[-8000:], flush=True)
                except (RuntimeError, subprocess.TimeoutExpired) as error:
                    print("Rehearsal diagnostics unavailable:", type(error).__name__, flush=True)
            raise
        finally:
            if started:
                # Only this fresh UUID-named project and its own volumes.
                compose("down", "--volumes", "--timeout", "35", timeout=180)
                print("Cleanup: isolated monitoring project and its volumes removed.", flush=True)


if __name__ == "__main__":
    main()
