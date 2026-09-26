#!/usr/bin/env python3
"""Create a non-overwriting custom-format backup from the Compose PostgreSQL service."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")


def checked_identifier(value, label):
    if not IDENTIFIER.fullmatch(value):
        raise SystemExit(f"{label} must be a simple PostgreSQL identifier.")
    return value


def exec_compose(service, *args, stdout=None, stdin=None):
    command = ["docker", "compose", "exec", "-T", service, *args]
    result = subprocess.run(
        command,
        stdin=stdin,
        stdout=subprocess.PIPE if stdout is None else stdout,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"{' '.join(command[:5])} {args[0]} failed ({result.returncode}): {detail}"
        )
    return result


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new backup archive path")
    parser.add_argument("--database", default="emergency_platform")
    parser.add_argument("--service", default="postgres")
    parser.add_argument("--user", default="emergency")
    args = parser.parse_args()
    database = checked_identifier(args.database, "Database name")
    service = checked_identifier(args.service, "Compose service")
    user = checked_identifier(args.user, "Database user")

    destination = args.destination.expanduser()
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    manifest_path = destination.with_name(destination.name + ".manifest.json")
    if destination.exists() or destination.is_symlink():
        raise SystemExit(f"Refusing to overwrite existing backup: {destination}")
    if manifest_path.exists() or manifest_path.is_symlink():
        raise SystemExit(f"Refusing to overwrite existing manifest: {manifest_path}")

    dump_fd, dump_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=parent)
    manifest_fd, manifest_name = tempfile.mkstemp(
        prefix=f".{manifest_path.name}.", dir=parent, text=True
    )
    dump_temp, manifest_temp = Path(dump_name), Path(manifest_name)
    dump_published = False
    try:
        os.fchmod(dump_fd, 0o600)
        with os.fdopen(dump_fd, "wb") as output:
            exec_compose(
                service,
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                f"--username={user}",
                f"--dbname={database}",
                stdout=output,
            )
            output.flush()
            os.fsync(output.fileno())

        size_bytes = dump_temp.stat().st_size
        if size_bytes == 0:
            raise RuntimeError("pg_dump produced an empty archive.")
        version = exec_compose(service, "pg_dump", "--version").stdout
        metadata = {
            "format": "postgresql-custom",
            "created_at": datetime.now(UTC).isoformat(),
            "database": database,
            "compose_service": service,
            "dump_tool": version.decode("utf-8", errors="replace").strip(),
            "size_bytes": size_bytes,
            "sha256": sha256_file(dump_temp),
        }
        os.fchmod(manifest_fd, 0o600)
        with os.fdopen(manifest_fd, "w", encoding="utf-8") as manifest:
            json.dump(metadata, manifest, indent=2)
            manifest.write("\n")
            manifest.flush()
            os.fsync(manifest.fileno())

        # Hard-link publication is atomic and fails if another process created the path.
        os.link(dump_temp, destination)
        dump_published = True
        os.link(manifest_temp, manifest_path)
    except Exception as exc:
        if dump_published:
            destination.unlink(missing_ok=True)
        if isinstance(exc, RuntimeError):
            raise SystemExit(str(exc)) from None
        raise
    finally:
        dump_temp.unlink(missing_ok=True)
        manifest_temp.unlink(missing_ok=True)

    print(f"Backup created: {destination}")
    print(f"Metadata: {manifest_path}")
    print(f"Size: {size_bytes} bytes; SHA-256: {metadata['sha256']}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Backup failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
