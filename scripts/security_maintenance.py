"""Bounded source-dependency audits. Preserve findings; fail closed on scan errors."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def audit_findings(kind: str, report: dict, returncode: int) -> tuple[int, int]:
    """Return (blocking, other) advisories; reject missing/incomplete reports."""
    if returncode not in (0, 1) or not isinstance(report, dict) or report.get("error"):
        raise ValueError(f"{kind} scan failed")
    if kind == "pip":
        dependencies = report.get("dependencies")
        if not isinstance(dependencies, list) or not dependencies:
            raise ValueError("pip-audit returned no dependency inventory")
        if any("vulns" not in item or item.get("skip_reason") for item in dependencies):
            raise ValueError("pip-audit skipped a dependency")
        # PyPI advisories do not supply severity. Trivy supplies the severity gate.
        findings = [vuln for item in dependencies for vuln in item["vulns"]]
        blocking = 0
    elif kind == "npm":
        inventory = report.get("metadata", {}).get("dependencies", {}).get("total", 0)
        if not inventory or not isinstance(report.get("vulnerabilities"), dict):
            raise ValueError("npm audit returned no dependency inventory")
        findings = list(report["vulnerabilities"].values())
        blocking = sum(
            item["severity"] in ("high", "critical") and bool(item.get("fixAvailable"))
            for item in findings
        )
    elif kind == "trivy":
        if returncode != 0 or report.get("SchemaVersion") != 2:
            raise ValueError("Trivy scan failed")
        results = report.get("Results", [])
        types = {item.get("Type") for item in results if item.get("Packages")}
        if not {"pip", "npm"}.issubset(types):
            raise ValueError("Trivy did not inventory both Python and npm dependencies")
        findings = [vuln for item in results for vuln in item.get("Vulnerabilities", []) or []]
        blocking = sum(
            item["Severity"] in ("HIGH", "CRITICAL") and bool(item.get("FixedVersion"))
            for item in findings
        )
    else:
        raise ValueError(f"Unknown audit kind: {kind}")
    if kind != "trivy" and returncode == 1 and not findings:
        raise ValueError(f"{kind} exited unsuccessfully without vulnerability findings")
    return blocking, len(findings) - blocking


def scan(command: list[str], output: Path) -> int:
    # Scanner diagnostics stay in the job log. Only JSON is uploaded.
    with output.open("w") as stream:
        return subprocess.run(command, cwd=ROOT, stdout=stream, timeout=360).returncode


def run_audits(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    failed = False
    with tempfile.TemporaryDirectory(prefix="emergency-dependencies-") as temporary:
        manifests = Path(temporary)
        # Only manifests enter the scanner, never local .env, secrets or caches.
        pins = []
        for name in ("requirements.txt", "requirements-dev.txt"):
            pins.extend(
                line for line in (ROOT / "backend" / name).read_text().splitlines()
                if line.strip() and not line.startswith(("#", "-r "))
            )
        (manifests / "requirements.txt").write_text("\n".join(pins) + "\n")
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(ROOT / "frontend" / name, manifests / name)
        commands = {
            "pip": [sys.executable, "-m", "pip_audit", "--strict", "--no-deps", "--disable-pip",
                    "-r", str(manifests / "requirements.txt"), "--format", "json"],
            "npm": ["npm", "--prefix", str(ROOT / "frontend"), "audit", "--json"],
            "trivy": ["trivy", "fs", "--scanners", "vuln", "--list-all-pkgs",
                      "--include-dev-deps", "--timeout", "5m", "--format", "json", str(manifests)],
        }
        for kind, command in commands.items():
            path = output / f"{kind}.json"
            try:
                code = scan(command, path)
                blocking, warnings = audit_findings(kind, json.loads(path.read_text()), code)
                results[kind] = {"blocking": blocking, "warnings": warnings}
                print(f"{kind}: {blocking} blocking, {warnings} warning advisories", flush=True)
                if warnings:
                    print(f"::warning::{kind}: review {warnings} advisories in the security artifact")
                failed |= blocking > 0
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as error:
                # Do not interpret scanner/network/schema failure as a clean scan.
                results[kind] = {"error": type(error).__name__}
                print(f"::error::{kind} audit could not complete ({type(error).__name__}); inspect scanner log")
                failed = True
    (output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    return int(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run_audits(parser.parse_args().output.resolve()))
