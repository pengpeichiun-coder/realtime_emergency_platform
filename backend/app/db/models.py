"""Tenant-ready relational records. Domain rules remain in services."""
from datetime import UTC, datetime
from uuid import uuid4
from typing import Any, get_args
from geoalchemy2 import Geography, WKBElement
from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Integer, BigInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from app.domain.models import IncidentStatus, IncidentType, Severity, UnitStatus, UnitType, DispatchStatus


def utcnow():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


def allowed(column, values):
    choices = ', '.join(repr(value) for value in get_args(values))
    return CheckConstraint(f'{column} IN ({choices})', name=f'ck_{column}_values')


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Organization(Timestamps, Base):
    __tablename__ = 'organizations'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    realtime_stream_id: Mapped[str] = mapped_column(String(80), default=lambda: uuid4().hex, server_default=text('gen_random_uuid()::text'))
    realtime_sequence: Mapped[int] = mapped_column(BigInteger, default=0, server_default='0')


class OperationalRecord:
    organization_id: Mapped[str] = mapped_column(String(80), ForeignKey('organizations.id'), primary_key=True)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)


class IncidentRecord(OperationalRecord, Timestamps, Base):
    __tablename__ = 'incidents'
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(120))
    area: Mapped[str] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[WKBElement] = mapped_column(Geography('POINT', srid=4326, spatial_index=False))
    assigned_unit_id: Mapped[str | None] = mapped_column(String(80))
    response_time_seconds: Mapped[int | None] = mapped_column(Integer)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        allowed('type', IncidentType), allowed('severity', Severity), allowed('status', IncidentStatus),
        ForeignKeyConstraint(['organization_id', 'assigned_unit_id'], ['units.organization_id', 'units.id'],
                             name='fk_incident_assigned_unit', use_alter=True, deferrable=True, initially='DEFERRED'),
        Index('ix_incidents_org_status', 'organization_id', 'status'),
        Index('ix_incidents_location', 'location', postgresql_using='gist'),
    )


class UnitRecord(OperationalRecord, Timestamps, Base):
    __tablename__ = 'units'
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    area: Mapped[str] = mapped_column(String(255))
    location: Mapped[WKBElement] = mapped_column(Geography('POINT', srid=4326, spatial_index=False))
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    assigned_incident_id: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = (
        allowed('type', UnitType), allowed('status', UnitStatus),
        ForeignKeyConstraint(['organization_id', 'assigned_incident_id'], ['incidents.organization_id', 'incidents.id'],
                             name='fk_unit_assigned_incident', deferrable=True, initially='DEFERRED'),
        Index('ix_units_org_status', 'organization_id', 'status'),
        Index('ix_units_location', 'location', postgresql_using='gist'),
    )


class DispatchRecord(OperationalRecord, Base):
    __tablename__ = 'dispatches'
    incident_id: Mapped[str] = mapped_column(String(80))
    unit_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_latitude: Mapped[float] = mapped_column(Float)
    origin_longitude: Mapped[float] = mapped_column(Float)
    __table_args__ = (
        allowed('status', DispatchStatus),
        ForeignKeyConstraint(['organization_id', 'incident_id'], ['incidents.organization_id', 'incidents.id']),
        ForeignKeyConstraint(['organization_id', 'unit_id'], ['units.organization_id', 'units.id']),
        Index('ix_dispatch_incident', 'organization_id', 'incident_id'),
        Index('ix_dispatch_unit', 'organization_id', 'unit_id'),
        Index('uq_dispatch_active_unit', 'organization_id', 'unit_id', unique=True,
              postgresql_where=text("status != 'completed'")),
        Index('uq_dispatch_active_incident', 'organization_id', 'incident_id', unique=True,
              postgresql_where=text("status != 'completed'")),
    )


class ActivityRecord(OperationalRecord, Base):
    __tablename__ = 'activity_logs'
    event_type: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column('metadata', JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index('ix_activity_org_created', 'organization_id', 'created_at'),
        Index('ix_activity_org_entity', 'organization_id', 'entity_id'),
    )


class UserRecord(Timestamps, Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True)
    __table_args__ = (
        CheckConstraint("role IN ('ADMIN','DISPATCHER','RESPONDER')", name='ck_users_role'),
        CheckConstraint('email = lower(email)', name='ck_users_email_normalized'),
    )


class RefreshTokenRecord(Base):
    __tablename__ = 'refresh_tokens'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    replaced_by: Mapped[str | None] = mapped_column(ForeignKey('refresh_tokens.id'))


class JobRecord(Timestamps, Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    job_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default='QUEUED')
    input_payload: Mapped[dict] = mapped_column(JSONB)
    result_payload: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    celery_task_id: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name='ck_jobs_status'),
        CheckConstraint('attempt_count >= 0', name='ck_jobs_attempts'),
        Index('ix_jobs_pending', 'status', 'next_attempt_at'),
    )


class WebhookSubscription(Timestamps, Base):
    __tablename__ = 'webhook_subscriptions'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    encrypted_signing_secret: Mapped[str] = mapped_column(Text)
    event_types: Mapped[list] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(Base):
    __tablename__ = 'outbox_events'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSONB)
    subscription_ids: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (
        Index('ix_outbox_pending_created', 'created_at',
              postgresql_where=text('processed_at IS NULL')),
    )


class WebhookDelivery(Timestamps, Base):
    __tablename__ = 'webhook_deliveries'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    subscription_id: Mapped[str] = mapped_column(ForeignKey('webhook_subscriptions.id', ondelete='CASCADE'))
    event_id: Mapped[str] = mapped_column(ForeignKey('outbox_events.id', ondelete='CASCADE'))
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSONB)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='PENDING')
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index('uq_delivery_event_subscription', 'event_id', 'subscription_id', unique=True),
        Index('ix_deliveries_pending', 'status', 'next_attempt_at'),
        CheckConstraint("status IN ('PENDING','RUNNING','DELIVERED','FAILED')", name='ck_delivery_status'),
        CheckConstraint('attempt_count >= 0', name='ck_delivery_attempts'),
    )


class OperationRetryAudit(Base):
    __tablename__ = 'operation_retry_audits'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'))
    requested_by_user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    operation_type: Mapped[str] = mapped_column(String(32))
    operation_id: Mapped[str] = mapped_column(String(80))
    retry_count: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text('now()'))
    __table_args__ = (
        CheckConstraint("operation_type IN ('JOB','WEBHOOK_DELIVERY')", name='ck_operation_retry_type'),
        CheckConstraint('retry_count BETWEEN 1 AND 3', name='ck_operation_retry_count'),
        Index('ix_operation_retries_scope', 'organization_id', 'operation_type', 'operation_id', 'created_at'),
    )


class AIArtifact(Timestamps, Base):
    __tablename__ = 'ai_artifacts'
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    # Historical source ID intentionally survives demo reset/deletion.
    incident_id: Mapped[str | None] = mapped_column(String(80))
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id', ondelete='CASCADE'), unique=True)
    artifact_type: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(80))
    content: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (CheckConstraint("artifact_type IN ('INCIDENT_SUMMARY','SHIFT_HANDOVER')", name='ck_ai_artifact_type'),)
