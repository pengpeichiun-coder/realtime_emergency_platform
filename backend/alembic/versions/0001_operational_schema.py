"""Initial durable operational schema with PostGIS.

Revision ID: 0001_operational_schema
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2

revision = '0001_operational_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS postgis')
    # Frozen schema: future model changes require a new revision.
    op.create_table('organizations',
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('activity_logs',
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=False),
    sa.Column('entity_id', sa.String(length=80), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('metadata', postgresql.JSONB(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('organization_id', sa.String(length=80), nullable=False),
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('organization_id', 'id')
    )
    op.create_index('ix_activity_org_created', 'activity_logs', ['organization_id', 'created_at'], unique=False)
    op.create_index('ix_activity_org_entity', 'activity_logs', ['organization_id', 'entity_id'], unique=False)
    op.create_table('incidents',
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('area', sa.String(length=120), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('location', geoalchemy2.types.Geography(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography', nullable=False), nullable=False),
    sa.Column('assigned_unit_id', sa.String(length=80), nullable=True),
    sa.Column('response_time_seconds', sa.Integer(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('organization_id', sa.String(length=80), nullable=False),
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("severity IN ('low', 'medium', 'high', 'critical')", name='ck_severity_values'),
    sa.CheckConstraint("status IN ('active', 'dispatched', 'on_scene', 'resolved')", name='ck_status_values'),
    sa.CheckConstraint("type IN ('fire', 'medical', 'traffic', 'security')", name='ck_type_values'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('organization_id', 'id')
    )
    op.create_index('ix_incidents_location', 'incidents', ['location'], unique=False, postgresql_using='gist')
    op.create_index('ix_incidents_org_status', 'incidents', ['organization_id', 'status'], unique=False)
    op.create_table('units',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('area', sa.String(length=255), nullable=False),
    sa.Column('location', geoalchemy2.types.Geography(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography', nullable=False), nullable=False),
    sa.Column('last_updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('assigned_incident_id', sa.String(length=80), nullable=True),
    sa.Column('organization_id', sa.String(length=80), nullable=False),
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('available', 'dispatched', 'en_route', 'on_scene', 'offline')", name='ck_status_values'),
    sa.CheckConstraint("type IN ('ambulance', 'fire_truck', 'police')", name='ck_type_values'),
    sa.ForeignKeyConstraint(['organization_id', 'assigned_incident_id'], ['incidents.organization_id', 'incidents.id'], name='fk_unit_assigned_incident', initially='DEFERRED', deferrable=True),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('organization_id', 'id')
    )
    op.create_index('ix_units_location', 'units', ['location'], unique=False, postgresql_using='gist')
    op.create_index('ix_units_org_status', 'units', ['organization_id', 'status'], unique=False)
    op.create_table('dispatches',
    sa.Column('incident_id', sa.String(length=80), nullable=False),
    sa.Column('unit_id', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('arrived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('origin_latitude', sa.Float(), nullable=False),
    sa.Column('origin_longitude', sa.Float(), nullable=False),
    sa.Column('organization_id', sa.String(length=80), nullable=False),
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.CheckConstraint("status IN ('created', 'en_route', 'arrived', 'completed')", name='ck_status_values'),
    sa.ForeignKeyConstraint(['organization_id', 'incident_id'], ['incidents.organization_id', 'incidents.id'], ),
    sa.ForeignKeyConstraint(['organization_id', 'unit_id'], ['units.organization_id', 'units.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('organization_id', 'id')
    )
    op.create_index('ix_dispatch_incident', 'dispatches', ['organization_id', 'incident_id'], unique=False)
    op.create_index('ix_dispatch_unit', 'dispatches', ['organization_id', 'unit_id'], unique=False)
    op.create_index('uq_dispatch_active_incident', 'dispatches', ['organization_id', 'incident_id'], unique=True, postgresql_where=sa.text("status != 'completed'"))
    op.create_index('uq_dispatch_active_unit', 'dispatches', ['organization_id', 'unit_id'], unique=True, postgresql_where=sa.text("status != 'completed'"))
    op.create_foreign_key('fk_incident_assigned_unit', 'incidents', 'units', ['organization_id', 'assigned_unit_id'], ['organization_id', 'id'], initially='DEFERRED', deferrable=True)


def downgrade():
    op.drop_constraint('fk_incident_assigned_unit', 'incidents', type_='foreignkey')
    op.drop_table('dispatches')
    op.drop_table('units')
    op.drop_table('incidents')
    op.drop_table('activity_logs')
    op.drop_table('organizations')
    # Keep the potentially shared PostGIS extension installed.
