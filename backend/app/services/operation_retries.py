"""Bounded, tenant-scoped audit records for explicit administrator retries."""

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.models import OperationRetryAudit
from app.observability import request_id_var

MAX_MANUAL_RETRIES = 3


async def record_manual_retry(session, context, operation_type, operation_id):
    count = await session.scalar(
        select(func.count(OperationRetryAudit.id)).where(
            OperationRetryAudit.organization_id == context.organization_id,
            OperationRetryAudit.operation_type == operation_type,
            OperationRetryAudit.operation_id == operation_id,
        )
    )
    if count >= MAX_MANUAL_RETRIES:
        raise HTTPException(409, "The manual retry limit has been reached.")

    retry_count = count + 1
    session.add(
        OperationRetryAudit(
            id="RETRY-" + uuid4().hex,
            organization_id=context.organization_id,
            requested_by_user_id=context.user_id,
            operation_type=operation_type,
            operation_id=operation_id,
            retry_count=retry_count,
            request_id=request_id_var.get(),
        )
    )
    return retry_count
