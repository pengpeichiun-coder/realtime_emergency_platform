"""Check alert data/label/runbook contracts; promtool tests the actual PromQL."""
import ast
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULES = yaml.safe_load((ROOT / "monitoring/alerts.yml").read_text())["groups"][0]["rules"]


def test_alert_metrics_are_real_and_durable_gauges_are_not_rates():
    source = ast.parse((ROOT / "backend/app/observability.py").read_text())
    metrics = {
        call.args[0].value for call in ast.walk(source)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        and call.func.id in {"Counter", "Histogram", "Gauge", "GaugeMetricFamily"}
        and call.args and isinstance(call.args[0], ast.Constant)
    } | {"up"}  # Prometheus emits target availability, not the application.
    for rule in RULES:
        selectors = re.findall(r"([a-z_][a-z_0-9]*)\{", rule["expr"])
        assert selectors and set(selectors) <= metrics
        if {"background_jobs", "webhook_deliveries"} & set(selectors):
            assert "max by" in rule["expr"]
            assert "rate(" not in rule["expr"] and "increase(" not in rule["expr"]


def test_alerts_keep_bounded_labels_and_wait_before_firing():
    for rule in RULES:
        assert set(rule["labels"]) == {"severity", "service"}
        groups = re.findall(r"by\s*\(([^)]+)\)", rule["expr"])
        assert groups
        for labels in groups:
            assert {name.strip() for name in labels.split(",")} <= {"cluster", "job", "job_type"}
        assert rule["for"] in {"2m", "3m", "5m"}
        assert "instance" not in str(rule)


def test_alerts_have_existing_runbook_anchors_and_required_ci():
    docs = (ROOT / "docs/operations.md").read_text()
    anchors = {line[3:].lower().replace(" ", "-") for line in docs.splitlines() if line.startswith("## ")}
    for rule in RULES:
        link = rule["annotations"]["runbook_url"]
        assert link.startswith("https://github.com/pengpeichiun-coder/realtime_emergency_platform/blob/main/docs/operations.md#")
        assert link.split("#")[1] in anchors
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert "make monitoring-validate" in str(ci["jobs"]["infrastructure"])
    assert "monitoring-validate: alerts-check" in (ROOT / "Makefile").read_text()
