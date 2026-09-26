#!/usr/bin/env bash
# Never suppress findings: archive everything, fail only fixable HIGH/CRITICAL.
set -euo pipefail
report_dir="${SECURITY_REPORT_DIR:-/tmp/emergency-security}"
trivy_bin="${TRIVY_BIN:-trivy}"
if ! command -v "$trivy_bin" >/dev/null 2>&1; then
  echo "Trivy executable not found: $trivy_bin" >&2
  exit 2
fi
mkdir -p "$report_dir"
failed=0
for component in backend frontend; do
  image="emergency-platform-${component}:local"
  "$trivy_bin" image --scanners vuln --timeout 10m --format json --output "$report_dir/$component.json" "$image"
  if ! "$trivy_bin" image --scanners vuln --timeout 10m --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "$image"; then
    failed=1
  fi
done
exit "$failed"
