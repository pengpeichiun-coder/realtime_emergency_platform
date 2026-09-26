"""Guard the maintenance gates and scanner failure policy, without cloud access."""
import importlib.util
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load_script("security_maintenance")
sbom = load_script("generate_image_sbom")


def test_dependabot_limits_noise_and_maintains_public_dependencies():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert config["version"] == 2
    updates = {item["package-ecosystem"]: item for item in config["updates"]}
    assert set(updates) == {"pip", "npm", "github-actions", "docker", "docker-compose", "terraform"}
    for update in updates.values():
        assert update["schedule"]["interval"] == "weekly"
        assert 1 <= update["open-pull-requests-limit"] <= 2
        assert "ignore" not in update
        for group in update["groups"].values():
            assert group["update-types"] == ["minor", "patch"]
    assert updates["docker-compose"]["allow"] == [{"dependency-name": "redis"}]
    assert "exclude-paths" not in updates["docker-compose"]


def test_workflows_parse_and_keep_immutable_actions_and_five_checks():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = yaml.safe_load(path.read_text())
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert re.fullmatch(r"[^@]+@[a-f0-9]{40}", step["uses"]), (path, step)
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert set(ci["jobs"]) == {
        "backend", "frontend", "containers", "infrastructure", "workflow-lint"
    }
    assert "scripts/generate_image_sbom.py" in str(ci["jobs"]["containers"])
    assert set(ci[True]) == {"pull_request", "workflow_dispatch"}
    assert ci["permissions"] == {"contents": "read"}
    assert {p.name for p in (ROOT / ".github/workflows").glob("*.yml")} == {
        "ci.yml", "security-maintenance.yml",
    }
    security = yaml.safe_load((ROOT / ".github/workflows/security-maintenance.yml").read_text())
    # PyYAML's YAML 1.1 loader parses the Actions `on` key as True.
    assert set(security[True]) == {"schedule", "workflow_dispatch"}
    assert security["permissions"] == {"contents": "read"}
    assert security["jobs"]["dependency-security"]["timeout-minutes"] <= 20
    assert "docker build" not in str(security)


@pytest.mark.parametrize("severity,fixed,expected", [
    ("CRITICAL", "2.0", (1, 0)), ("HIGH", "2.0", (1, 0)),
    ("HIGH", "", (0, 1)), ("LOW", "2.0", (0, 1)),
])
def test_trivy_blocks_only_fixable_high_or_critical(severity, fixed, expected):
    report = {"SchemaVersion": 2, "Results": [
        {"Type": "pip", "Packages": [{"Name": "example"}], "Vulnerabilities": [
            {"Severity": severity, "FixedVersion": fixed}
        ]}, {"Type": "npm", "Packages": [{"Name": "example"}]},
    ]}
    assert audit.audit_findings("trivy", report, 0) == expected


def test_npm_severity_and_fixability_gate():
    report = {"metadata": {"dependencies": {"total": 2}}, "vulnerabilities": {
        "one": {"severity": "high", "fixAvailable": {"version": "2"}},
        "two": {"severity": "critical", "fixAvailable": False},
    }}
    assert audit.audit_findings("npm", report, 1) == (1, 1)


def test_pip_advisories_without_severity_remain_visible():
    report = {"dependencies": [{"name": "example", "vulns": [{"id": "example"}]}]}
    assert audit.audit_findings("pip", report, 1) == (0, 1)


@pytest.mark.parametrize("kind,report,code", [
    ("pip", {}, 0), ("npm", {"error": {"code": "network"}}, 1),
    ("trivy", {"SchemaVersion": 2, "Results": []}, 0),
    ("pip", {"dependencies": [{"skip_reason": "unavailable"}]}, 0),
    ("pip", {"dependencies": [{"vulns": []}]}, 1),
    ("npm", {"vulnerabilities": {}}, 0), ("trivy", {}, 2),
])
def test_scanner_errors_or_missing_inventory_never_pass(kind, report, code):
    with pytest.raises(ValueError):
        audit.audit_findings(kind, report, code)


@pytest.mark.parametrize("document", [
    {}, {"bomFormat": "CycloneDX", "components": []},
    {"bomFormat": "CycloneDX", "components": [{}]},
])
def test_sbom_requires_real_inventory(document):
    with pytest.raises(ValueError):
        sbom.validate_sbom(document)


def test_sbom_accepts_cyclonedx_inventory():
    assert sbom.validate_sbom({"bomFormat": "CycloneDX", "components": [{"name": "react"}]}) == 1
