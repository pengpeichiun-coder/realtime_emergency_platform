"""Generate and validate the API's versioned OpenAPI snapshot."""

import argparse
import json
from pathlib import Path
import sys

from app.main import app


RELEASE_VERSION = "1.0.0"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "docs/api/openapi-v1.0.0.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def resolve_pointer(document: dict, reference: str) -> object:
    if not reference.startswith("#/"):
        raise ValueError(f"External OpenAPI reference is not supported: {reference}")
    value: object = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"Unresolved OpenAPI reference: {reference}")
        value = value[part]
    return value


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def validate(document: dict) -> None:
    if not str(document.get("openapi", "")).startswith("3.1."):
        raise ValueError("Expected an OpenAPI 3.1 document.")
    info = document.get("info", {})
    if info.get("title") != "Emergency Response Platform API":
        raise ValueError("OpenAPI title does not match the FastAPI application.")
    if info.get("version") != RELEASE_VERSION:
        raise ValueError(f"OpenAPI info.version must be {RELEASE_VERSION}.")

    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ValueError("OpenAPI paths must be a non-empty object.")
    required = {"/health", "/api/incidents", "/api/jobs", "/api/webhooks"}
    if missing := required - paths.keys():
        raise ValueError(f"Required API paths are missing: {', '.join(sorted(missing))}")

    operation_count = 0
    for path, path_item in paths.items():
        if not path.startswith("/") or not isinstance(path_item, dict):
            raise ValueError(f"Invalid OpenAPI path item: {path!r}")
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            if not isinstance(operation, dict) or not operation.get("responses"):
                raise ValueError(f"{method.upper()} {path} has no response definitions.")
            operation_count += 1
    if operation_count == 0:
        raise ValueError("OpenAPI document contains no HTTP operations.")

    for node in walk(document):
        if "$ref" in node:
            resolve_pointer(document, node["$ref"])


def render() -> str:
    document = app.openapi()
    validate(document)
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the generated document and fail if the committed snapshot is stale",
    )
    parser.add_argument(
        "--output",
        help="write to this path, or '-' for stdout (default: docs/api/openapi-v1.0.0.json)",
    )
    args = parser.parse_args()

    try:
        generated = render()
        if args.check:
            target = Path(args.output) if args.output else DEFAULT_OUTPUT
            if not target.is_file() or target.read_text() != generated:
                print(f"OpenAPI snapshot is missing or stale: {target}", file=sys.stderr)
                return 1
            print(f"OpenAPI 3.1 snapshot is valid and current: {target}")
            return 0

        if args.output == "-":
            sys.stdout.write(generated)
        else:
            target = Path(args.output) if args.output else DEFAULT_OUTPUT
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(generated)
            print(f"Wrote validated OpenAPI snapshot: {target}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"OpenAPI export failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
