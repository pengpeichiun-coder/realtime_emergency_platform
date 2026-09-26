"""Shared release/PR SBOM generation; never publishes or modifies an image."""
import argparse
import json
from pathlib import Path
import subprocess


def validate_sbom(document: dict) -> int:
    components = document.get("components")
    if document.get("bomFormat") != "CycloneDX" or not isinstance(components, list) or not components:
        raise ValueError("Expected a nonempty CycloneDX component inventory")
    if any(not isinstance(item, dict) or not item.get("name") for item in components):
        raise ValueError("Invalid CycloneDX component")
    return len(components)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "trivy", "image", "--scanners", "vuln", "--timeout", "5m",
        "--format", "cyclonedx", "--output", str(args.output), args.image,
    ], check=True, timeout=360)
    count = validate_sbom(json.loads(args.output.read_text()))
    print(f"Validated {count} CycloneDX components: {args.output.name}")


if __name__ == "__main__":
    main()
