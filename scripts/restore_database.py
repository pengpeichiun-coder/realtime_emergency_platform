#!/usr/bin/env python3
"""Restore a verified Compose backup to a database name that does not exist."""

import argparse
import json
from pathlib import Path
import sys

from backup_database import checked_identifier, exec_compose, sha256_file


def query(service, user, database, sql):
    result = exec_compose(
        service,
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        f"--username={user}",
        f"--dbname={database}",
        "--command",
        sql,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("target_database")
    parser.add_argument("--service", default="postgres")
    parser.add_argument("--admin-user", default="emergency")
    args = parser.parse_args()

    archive = args.archive.expanduser().resolve(strict=True)
    service = checked_identifier(args.service, "Compose service")
    admin = checked_identifier(args.admin_user, "Database user")
    target = checked_identifier(args.target_database, "Database name")
    manifest_path = archive.with_name(archive.name + ".manifest.json")
    if not manifest_path.is_file():
        raise SystemExit(f"Backup manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "postgresql-custom":
        raise SystemExit("Backup manifest format is not supported.")
    if manifest.get("database") == target:
        raise SystemExit("Restore target must differ from the source database.")
    actual_hash = sha256_file(archive)
    if actual_hash != manifest.get("sha256") or archive.stat().st_size != manifest.get(
        "size_bytes"
    ):
        raise SystemExit("Backup archive does not match its SHA-256 manifest.")

    with archive.open("rb") as source:
        listing = exec_compose(service, "pg_restore", "--list", stdin=source)
    if not listing.stdout.strip():
        raise SystemExit("Backup archive contains no restorable entries.")

    exists = query(
        service,
        admin,
        "postgres",
        "SELECT 1 FROM pg_database WHERE datname = '" + target + "'",
    )
    if exists:
        raise SystemExit(f"Refusing to overwrite existing database: {target}")

    exec_compose(
        service,
        "createdb",
        f"--username={admin}",
        "--template=template0",
        target,
    )
    try:
        with archive.open("rb") as source:
            exec_compose(
                service,
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                f"--username={admin}",
                f"--dbname={target}",
                stdin=source,
            )
    except RuntimeError as exc:
        raise SystemExit(
            f"Restore failed; partial database {target} is retained for inspection. {exc}"
        ) from None

    postgis = query(service, admin, target, "SELECT PostGIS_Full_Version()")
    migration = query(
        service,
        admin,
        target,
        "SELECT version_num FROM alembic_version LIMIT 1",
    )
    geography_count = int(
        query(
            service,
            admin,
            target,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name IN ('incidents','units') "
            "AND column_name='location' AND udt_name='geography'",
        )
    )
    gist_count = int(
        query(
            service,
            admin,
            target,
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
            "AND tablename IN ('incidents','units') AND indexdef ILIKE '%USING gist%'",
        )
    )
    if geography_count < 2 or gist_count < 2 or not postgis or not migration:
        raise SystemExit(
            "Restored schema check failed; the fresh target database is retained."
        )

    print(f"Restored {manifest['database']} into fresh database {target}.")
    print(f"Alembic revision: {migration}")
    print(f"PostGIS: {postgis}")
    print(f"Geography columns: {geography_count}; GiST indexes: {gist_count}")
    print("The restore target was newly created; no existing database was replaced.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"Restore failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
