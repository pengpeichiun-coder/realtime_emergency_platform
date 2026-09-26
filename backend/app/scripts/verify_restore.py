"""Exercise the restored database through the application's runtime SQLAlchemy URL."""

import argparse
import asyncio
import json
import os

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.db.session import Database


TABLES = (
    "organizations",
    "users",
    "incidents",
    "units",
    "dispatches",
    "activity_logs",
    "jobs",
    "webhook_subscriptions",
    "outbox_events",
    "webhook_deliveries",
    "ai_artifacts",
)


async def verify(database_name):
    url = make_url(os.environ["DATABASE_URL"]).set(database=database_name)
    database = Database(url.render_as_string(hide_password=False))
    try:
        async with database.sessions() as session:
            role = (
                await session.execute(
                    text(
                        "SELECT current_user, "
                        "has_schema_privilege(current_user, 'public', 'CREATE')"
                    )
                )
            ).one()
            if role != ("emergency_app", False):
                raise RuntimeError("Restored runtime role privilege check failed.")

            counts = {
                table: await session.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in TABLES
            }
            missing = [table for table, count in counts.items() if not count]
            if missing:
                raise RuntimeError(
                    "The restored application fixture is missing table data: "
                    + ", ".join(missing)
                )

            postgis = await session.scalar(text("SELECT PostGIS_Full_Version()"))
            migration = await session.scalar(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            geography_count = await session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name IN ('incidents','units') "
                    "AND column_name='location' AND udt_name='geography'"
                )
            )
            gist_count = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename IN ('incidents','units') "
                    "AND indexdef ILIKE '%USING gist%'"
                )
            )
            nearby_count = await session.scalar(
                text(
                    "SELECT count(*) FROM incidents i JOIN units u "
                    "ON u.organization_id=i.organization_id "
                    "WHERE i.status IN ('active','on_scene') AND u.status='available' "
                    "AND u.assigned_incident_id IS NULL "
                    "AND ST_DWithin(i.location,u.location,50000)"
                )
            )
            if (
                not postgis
                or not migration
                or geography_count < 2
                or gist_count < 2
                or not nearby_count
            ):
                raise RuntimeError("Restored PostGIS/application query check failed.")

        print(
            json.dumps(
                {
                    "database": database_name,
                    "runtime_role": role[0],
                    "runtime_schema_create": role[1],
                    "alembic_revision": migration,
                    "postgis": postgis,
                    "geography_columns": geography_count,
                    "gist_indexes": gist_count,
                    "nearby_unit_rows": nearby_count,
                    "application_rows": counts,
                },
                indent=2,
            )
        )
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    args = parser.parse_args()
    asyncio.run(verify(args.database))


if __name__ == "__main__":
    main()
