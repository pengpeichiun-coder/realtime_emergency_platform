from datetime import datetime
from typing import Annotated
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, ConfigDict
from app.auth.policies import Context, admin, operator
from app.context import RequestContext
from app.services.jobs import JobService

router = APIRouter(prefix="/api", tags=["jobs"])


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    created_by_user_id: str
    job_type: str
    status: str
    input_payload: dict
    result_payload: dict | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


def job_service(request: Request):
    database = request.app.state.tenants.database
    if database is None:
        raise HTTPException(503, "Jobs require PostgreSQL.")
    return JobService(database)


Service = Annotated[JobService, Depends(job_service)]
Operator = Annotated[RequestContext, Depends(operator)]
Admin = Annotated[RequestContext, Depends(admin)]


class RetryAuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    requested_by_user_id: str
    operation_type: str
    operation_id: str
    retry_count: int
    created_at: datetime


@router.post("/incidents/{incident_id}/reports", status_code=202)
async def report(incident_id: str, context: Operator, service: Service):
    return {
        "job_id": (await service.create(context, "INCIDENT_REPORT", incident_id)).id
    }


@router.get("/jobs", response_model=list[JobResponse])
async def jobs(context: Context, service: Service):
    return await service.list(context)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def job(job_id: str, context: Context, service: Service):
    return await service.get(context, job_id)


@router.post("/jobs/{job_id}/retry", status_code=202, response_model=JobResponse)
async def retry_job(job_id: str, context: Admin, service: Service):
    return await service.retry_failed(context, job_id)


@router.get("/operation-retries", response_model=list[RetryAuditResponse])
async def retry_history(context: Admin, service: Service):
    return await service.retry_history(context)
