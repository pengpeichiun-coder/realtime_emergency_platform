"""Provision separate non-superuser migration/runtime database roles.

This explicit bootstrap uses a local Compose bootstrap URL or RDS master
credentials. API, Celery, publisher and migration tasks use separate URLs afterward.
"""

import argparse
import asyncio
import os
import re

import asyncpg
from sqlalchemy.engine import make_url

from app.db.session import database_connect_args


def parse_application_urls(database_name="emergency_platform"):
    runtime = make_url(os.environ["DATABASE_URL"]).set(database=database_name)
    migration = make_url(os.environ["MIGRATION_DATABASE_URL"]).set(
        database=database_name
    )
    expected = {"emergency_app", "emergency_migrator"}
    if (
        runtime.username != "emergency_app"
        or migration.username != "emergency_migrator"
        or runtime.database != database_name
        or migration.database != runtime.database
        or runtime.host != migration.host
        or (runtime.port or 5432) != (migration.port or 5432)
    ):
        raise ValueError("Bootstrap requires the dedicated application and migration roles")
    if expected != {runtime.username, migration.username}:
        raise ValueError("Unexpected application role configuration")
    if not runtime.password or not migration.password:
        raise ValueError("Both application role passwords are required")
    return runtime, migration


async def _set_role_password(connection, name: str, password: str):
    quoted = await connection.fetchval("SELECT quote_literal($1::text)", password)
    exists = await connection.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", name)
    attributes = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
    if exists:
        await connection.execute(
            f"ALTER ROLE {name} WITH {attributes} PASSWORD {quoted}"
        )
    else:
        await connection.execute(
            f"CREATE ROLE {name} {attributes} PASSWORD {quoted}"
        )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


async def _assign_application_objects(connection):
    relations = await connection.fetch(
        """
        SELECT c.relname, c.relkind::text AS relkind
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS d
              WHERE d.classid = 'pg_class'::regclass
                AND d.objid = c.oid AND d.deptype = 'e'
          )
        """
    )
    for relation in relations:
        name = "public." + _quote_identifier(relation["relname"])
        command = {
            "r": "ALTER TABLE",
            "p": "ALTER TABLE",
            "S": "ALTER SEQUENCE",
            "v": "ALTER VIEW",
            "m": "ALTER MATERIALIZED VIEW",
            "f": "ALTER FOREIGN TABLE",
        }[relation["relkind"]]
        await connection.execute(f"{command} {name} OWNER TO emergency_migrator")


async def initialize(database_name="emergency_platform"):
    runtime, migration = parse_application_urls(database_name)
    local_admin = os.environ.get("BOOTSTRAP_DATABASE_URL")
    if local_admin:
        admin = make_url(local_admin).set(database=database_name)
        if admin.username != "emergency" or admin.database != runtime.database:
            raise ValueError("Local bootstrap must use the Compose bootstrap role")
        username, password = admin.username, admin.password
    else:
        username = os.environ.get("RDS_USERNAME")
        password = os.environ.get("RDS_PASSWORD")
        if not username or not password:
            raise ValueError("RDS master credentials are required for cloud bootstrap")

    connection = await asyncpg.connect(
        host=runtime.host,
        port=runtime.port or 5432,
        database=runtime.database,
        user=username,
        password=password,
        **database_connect_args(),
        timeout=10,
    )
    try:
        async with connection.transaction():
            await connection.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            await _set_role_password(connection, runtime.username, runtime.password)
            await _set_role_password(connection, migration.username, migration.password)
            await connection.execute(
                "GRANT CONNECT ON DATABASE "
                + _quote_identifier(runtime.database)
                + " TO emergency_app, emergency_migrator"
            )
            await connection.execute(
                "ALTER SCHEMA public OWNER TO emergency_migrator"
            )
            await connection.execute(
                "REVOKE CREATE ON SCHEMA public FROM PUBLIC, emergency_app"
            )
            await connection.execute(
                "GRANT USAGE, CREATE ON SCHEMA public TO emergency_migrator"
            )
            await connection.execute(
                "GRANT USAGE ON SCHEMA public TO emergency_app"
            )
            await _assign_application_objects(connection)
            await connection.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO emergency_app"
            )
            await connection.execute(
                "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO emergency_app"
            )
            await connection.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE emergency_migrator IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO emergency_app"
            )
            await connection.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE emergency_migrator IN SCHEMA public "
                "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO emergency_app"
            )
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-role", required=True, action="store_true")
    parser.add_argument("--database", default="emergency_platform")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", args.database):
        raise SystemExit("Database name must be a simple PostgreSQL identifier.")
    try:
        asyncio.run(initialize(args.database))
    except Exception as exc:
        # asyncpg/SQLAlchemy exception bodies can include connection details.
        raise SystemExit("Database role bootstrap failed: " + type(exc).__name__) from None
    print("Separated database roles and PostGIS are ready; run Alembic as emergency_migrator.")


if __name__ == "__main__":
    main()
