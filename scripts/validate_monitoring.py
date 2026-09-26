"""Validate the real configs with official tools, using a temporary token fixture."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promtool", default="promtool")
    parser.add_argument("--amtool", default="amtool")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="emergency-monitoring-check-") as directory:
        staging = Path(directory)
        for name in ("alerts.yml", "alerts.rehearsal.yml"):
            shutil.copyfile(ROOT / "monitoring" / name, staging / name)
        token = staging / "metrics_token"
        token.write_text("validation-only-not-a-live-credential")
        token.chmod(0o600)
        source = (ROOT / "monitoring/prometheus.yml").read_text()
        credential = "credentials_file: /run/monitoring/metrics_token"
        if source.count(credential) != 1:
            raise ValueError("Expected exactly one file-backed metrics credential")
        config = staging / "prometheus.yml"
        config.write_text(source.replace(credential, "credentials_file: " + json.dumps(str(token))))
        subprocess.run([args.promtool, "check", "config", str(config)], check=True)
        subprocess.run([args.promtool, "check", "rules", str(staging / "alerts.rehearsal.yml")], check=True)
        subprocess.run([args.amtool, "check-config", str(ROOT / "monitoring/alertmanager.yml")], check=True)


if __name__ == "__main__":
    main()
