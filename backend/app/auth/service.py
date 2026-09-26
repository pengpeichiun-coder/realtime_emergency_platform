"""Credential transactions; logs contain event names and internal IDs only."""

from datetime import UTC, datetime, timedelta
import logging
from secrets import token_urlsafe
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from app.auth.crypto import (
    access_token,
    claims,
    dummy_hash,
    hasher,
    token_hash,
    verify_password,
)
from app.context import RequestContext
from app.db.models import Organization, UserRecord, RefreshTokenRecord

logger = logging.getLogger(__name__)


def identifier(prefix):
    return prefix + "-" + uuid4().hex


class AuthService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings

    async def identity(self, raw):
        value = claims(raw, self.settings)
        async with self.database.sessions() as session:
            user = await session.get(UserRecord, value["sub"])
            if user is None or not user.is_active:
                raise HTTPException(401, "Invalid or inactive identity.")
            return user, value

    def issue(self, session, user):
        raw = token_urlsafe(48)
        row = RefreshTokenRecord(
            id=identifier("REF"),
            user_id=user.id,
            token_hash=token_hash(raw),
            expires_at=datetime.now(UTC) + timedelta(days=self.settings.refresh_days),
        )
        session.add(row)
        result = {
            "access_token": access_token(user.id, self.settings),
            "token_type": "bearer",
            "expires_in": self.settings.access_minutes * 60,
        }
        return result, raw, row

    async def register(self, body):
        encoded = await run_in_threadpool(hasher.hash, body.password.get_secret_value())
        try:
            async with self.database.sessions.begin() as session:
                organization = Organization(
                    id=identifier("ORG"), name=body.organization_name
                )
                session.add(organization)
                await session.flush()
                user = UserRecord(
                    id=identifier("USR"),
                    organization_id=organization.id,
                    email=str(body.email),
                    display_name=body.display_name,
                    password_hash=encoded,
                    role="ADMIN",
                    is_active=True,
                )
                session.add(user)
                await session.flush()
                result, raw, _ = self.issue(session, user)
            logger.info(
                "Registration succeeded user=%s organization=%s",
                user.id,
                organization.id,
            )
            return result, raw
        except IntegrityError:
            raise HTTPException(
                409, "Registration could not be completed with this email."
            ) from None

    async def login(self, body):
        async with self.database.sessions.begin() as session:
            user = await session.scalar(
                select(UserRecord)
                .where(UserRecord.email == str(body.email))
                .with_for_update()
            )
            valid = await run_in_threadpool(
                verify_password,
                user.password_hash if user else dummy_hash,
                body.password.get_secret_value(),
            )
            if not user or not valid or not user.is_active:
                logger.info("Login failed")
                raise HTTPException(401, "Invalid credentials.")
            if hasher.check_needs_rehash(user.password_hash):
                user.password_hash = await run_in_threadpool(
                    hasher.hash, body.password.get_secret_value()
                )
            result, raw, _ = self.issue(session, user)
        logger.info(
            "Login succeeded user=%s organization=%s", user.id, user.organization_id
        )
        return result, raw

    async def refresh(self, raw):
        digest = token_hash(raw or "")
        async with self.database.sessions.begin() as session:
            # Lock user first, then token: concurrent refresh and deactivation serialize.
            user_id = await session.scalar(
                select(RefreshTokenRecord.user_id).where(
                    RefreshTokenRecord.token_hash == digest
                )
            )
            user = (
                await session.scalar(
                    select(UserRecord).where(UserRecord.id == user_id).with_for_update()
                )
                if user_id
                else None
            )
            old = await session.scalar(
                select(RefreshTokenRecord)
                .where(RefreshTokenRecord.token_hash == digest)
                .with_for_update()
            )
            if (
                not user
                or not user.is_active
                or not old
                or old.revoked_at
                or old.expires_at <= datetime.now(UTC)
            ):
                logger.info("Refresh failed")
                raise HTTPException(401, "Invalid or expired session.")
            result, replacement, row = self.issue(session, user)
            await session.flush()
            old.revoked_at = datetime.now(UTC)
            old.replaced_by = row.id
        return result, replacement

    async def logout(self, raw):
        async with self.database.sessions.begin() as session:
            await session.execute(
                update(RefreshTokenRecord)
                .where(
                    RefreshTokenRecord.token_hash == token_hash(raw or ""),
                    RefreshTokenRecord.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )

    async def list_users(self, context):
        async with self.database.sessions() as session:
            return list(
                await session.scalars(
                    select(UserRecord)
                    .where(UserRecord.organization_id == context.organization_id)
                    .order_by(UserRecord.email)
                )
            )

    async def lock_admin(self, session, context):
        await session.execute(
            select(Organization.id)
            .where(Organization.id == context.organization_id)
            .with_for_update()
        )
        actor = await session.scalar(
            select(UserRecord)
            .where(
                UserRecord.id == context.user_id,
                UserRecord.organization_id == context.organization_id,
            )
            .with_for_update()
        )
        if not actor or not actor.is_active or actor.role != "ADMIN":
            raise HTTPException(403, "Administrator access required.")

    async def create_user(self, context, body):
        encoded = await run_in_threadpool(hasher.hash, body.password.get_secret_value())
        try:
            async with self.database.sessions.begin() as session:
                await self.lock_admin(session, context)
                user = UserRecord(
                    id=identifier("USR"),
                    organization_id=context.organization_id,
                    email=str(body.email),
                    display_name=body.display_name,
                    role=body.role,
                    password_hash=encoded,
                    is_active=True,
                )
                session.add(user)
                await session.flush()
            return user
        except IntegrityError:
            raise HTTPException(
                409, "User could not be created with this email."
            ) from None

    async def change_user(self, context, user_id, *, role=None, active=None):
        async with self.database.sessions.begin() as session:
            await self.lock_admin(session, context)
            user = await session.scalar(
                select(UserRecord)
                .where(
                    UserRecord.id == user_id,
                    UserRecord.organization_id == context.organization_id,
                )
                .with_for_update()
            )
            if user is None:
                raise HTTPException(404, "User was not found.")
            if (
                user.is_active
                and user.role == "ADMIN"
                and (active is False or (role and role != "ADMIN"))
            ):
                count = await session.scalar(
                    select(func.count())
                    .select_from(UserRecord)
                    .where(
                        UserRecord.organization_id == context.organization_id,
                        UserRecord.role == "ADMIN",
                        UserRecord.is_active.is_(True),
                    )
                )
                if count <= 1:
                    raise HTTPException(
                        409, "The organization must retain an active administrator."
                    )
            if role is not None:
                user.role = role
            if active is not None:
                user.is_active = active
                if not active:
                    await session.execute(
                        update(RefreshTokenRecord)
                        .where(
                            RefreshTokenRecord.user_id == user.id,
                            RefreshTokenRecord.revoked_at.is_(None),
                        )
                        .values(revoked_at=datetime.now(UTC))
                    )
            await session.flush()
        logger.info(
            "User %s actor=%s user=%s organization=%s",
            "role changed" if role else "active status changed",
            context.user_id,
            user.id,
            context.organization_id,
        )
        return user


def context_for(user):
    return RequestContext(user.id, user.organization_id, user.role)
