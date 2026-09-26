"""Publication checks must detect regressions without printing sensitive values."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("public_readiness", ROOT / "scripts/public_readiness.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize("value", [
    "/" + "Users/operator/project", "/" + "home/operator/project",
    "C:" + "\\Users\\operator\\project",
    "private.person" + "@" + "mail.invalid",
    "-----BEGIN " + "PRIVATE KEY-----",
])
def test_private_content_is_blocked_without_echoing_values(value):
    issues = audit.privacy_issues("example.md", value)
    assert issues and all(value not in issue for issue in issues)
    assert all("example.md:1:" in issue for issue in issues)


def test_demo_addresses_and_github_attribution_are_allowed():
    text = "admin@demo.example.com tester@example.test owner@users.noreply.github.com noreply@github.com"
    assert audit.privacy_issues("demo.md", text) == []
    assert audit.privacy_issues("demo.md", "personal" + "@" + "github.com")


@pytest.mark.parametrize("name", [
    ".env", "backend/.env.production", "infra/terraform.tfvars",
    "infra/state.tfstate.backup", "infra/backend.hcl", "backups/data.dump",
])
def test_local_configuration_cannot_be_tracked(name):
    assert audit.forbidden_file(name)


def test_examples_and_public_ca_remain_trackable():
    for name in (".env.example", "backend/.env.example", "infra/terraform.tfvars.example",
                 "infra/backend.hcl.example", "backend/app/db/rds-ca-bundle.pem"):
        assert not audit.forbidden_file(name)


def test_links_require_tracked_files_and_real_heading_anchors(tmp_path):
    files = {"README.md", "docs/guide.md", "docs/screen.png"}
    texts = {
        "README.md": '[Guide](docs/guide.md#same-1)\n![Screen](docs/screen.png)\n[Reference][guide]\n[guide]: docs/guide.md#same\n',
        "docs/guide.md": "# Same\n## Same\n```md\n[ignore](missing.md)\n```\n",
    }
    assert audit.markdown_issues(tmp_path, files, texts) == ([], 3)
    (tmp_path / "untracked.md").write_text("exists only on developer machine")
    texts["README.md"] += "[bad](untracked.md)\n[anchor](docs/guide.md#missing)\n[ref][undefined]\n[escape](../outside.md)\n"
    issues, _ = audit.markdown_issues(tmp_path, files, texts)
    assert len(issues) == 4
    assert {issue.split(": ", 1)[1] for issue in issues} == {
        "link target is not tracked", "missing Markdown heading anchor",
        "undefined link reference", "link escapes repository",
    }


def test_png_text_metadata_and_trailing_data_require_review():
    data = (ROOT / "docs/screenshots/01-login.png").read_bytes()
    assert audit.png_issues("screen.png", data) == []
    assert audit.png_issues("screen.png", data + b"private metadata")
    chunk = b"\x00\x00\x00\x00tEXt\x00\x00\x00\x00"
    assert audit.png_issues("screen.png", data[:8] + chunk + data[8:])


@pytest.mark.parametrize("code,report", [(2, []), (1, []), (0, [{}]), (0, {}), (0, None)])
def test_scanner_errors_and_inconsistent_reports_never_pass(tmp_path, monkeypatch, code, report):
    def run(command, **kwargs):
        if report is not None:
            Path(command[command.index("--report-path") + 1]).write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, code, stdout=b"", stderr=b"sensitive diagnostic")
    monkeypatch.setattr(audit.subprocess, "run", run)
    with pytest.raises(RuntimeError):
        audit.scan_secrets("gitleaks", "git", ROOT, ROOT, tmp_path)


def test_secret_findings_are_counted_without_logging_content(tmp_path, monkeypatch, capsys):
    marker = "sensitive synthetic match"
    def run(command, **kwargs):
        assert "--redact=100" in command
        assert "--ignore-gitleaks-allow" in command
        assert "--log-opts=--all --full-history" in command
        assert kwargs["capture_output"] and kwargs["timeout"] <= 200
        assert not any(k.startswith("GITLEAKS_") for k in kwargs["env"])
        Path(command[command.index("--report-path") + 1]).write_text(json.dumps([{"Secret": marker}]))
        return subprocess.CompletedProcess(command, 1, stdout=marker.encode(), stderr=marker.encode())
    monkeypatch.setenv("GITLEAKS_CONFIG", "untrusted-override")
    monkeypatch.setattr(audit.subprocess, "run", run)
    assert audit.scan_secrets("gitleaks", "git", ROOT, ROOT, tmp_path) == 1
    output = capsys.readouterr().out
    assert marker not in output and "BLOCKED: git secrets (1 findings)" in output


def test_secret_exceptions_are_rule_path_and_exact_content_scoped():
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
    assert config["extend"] == {"useDefault": True}
    assert len(config["allowlists"]) == 2
    for entry in config["allowlists"]:
        assert entry["condition"] == "AND" and entry["regexTarget"] in {"match", "line"}
        assert len(entry["targetRules"]) == len(entry["paths"]) == len(entry["regexes"]) == 1
        assert entry["paths"][0].endswith("$")
        assert entry["regexes"][0].startswith("^") and entry["regexes"][0].endswith("$")


def test_tracked_repository_privacy_and_markdown_links():
    files = set(filter(None, audit.git(ROOT, "ls-files", "-z").split("\0")))
    texts = {}
    for name in files:
        assert not audit.forbidden_file(name), name
        data = (ROOT / name).read_bytes()
        if name.endswith(".png"):
            assert audit.png_issues(name, data) == []
        try:
            texts[name] = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        assert audit.privacy_issues(name, texts[name]) == []
    issues, checked = audit.markdown_issues(ROOT, files, texts)
    assert issues == [] and checked > 0


@pytest.mark.parametrize("email,expected", [
    ("owner@users.noreply.github.com", 0),
    ("private.person" + "@" + "mail.invalid", 1),
])
def test_history_email_privacy_is_a_failing_gate(tmp_path, monkeypatch, email, expected):
    (tmp_path / "README.md").write_text("# Example repository\n")
    outputs = {
        ("ls-files", "-z"): "README.md\0",
        ("rev-parse", "--is-shallow-repository"): "false\n",
        ("log", "--all", "--format=%ae%n%ce"): email + "\n",
    }
    monkeypatch.setattr(audit, "git", lambda root, *args: outputs[args])
    monkeypatch.setattr(audit, "scan_secrets", lambda *args: 0)
    assert audit.check(tmp_path, "gitleaks") == expected
