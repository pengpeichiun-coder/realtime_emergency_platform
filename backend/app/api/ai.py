from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from app.api.jobs import Service, Operator
from app.auth.policies import Context
from app.db.models import AIArtifact

router = APIRouter(prefix="/api", tags=["AI advisory summaries"])


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    incident_id: str | None
    job_id: str
    artifact_type: str
    provider: str
    model: str
    prompt_version: str
    content: dict
    created_at: datetime


@router.post("/incidents/{incident_id}/ai-summary", status_code=202)
async def summary(incident_id: str, context: Operator, service: Service):
    return {
        "job_id": (await service.create(context, "INCIDENT_SUMMARY", incident_id)).id
    }


@router.post("/ai/shift-handover", status_code=202)
async def handover(context: Operator, service: Service):
    return {"job_id": (await service.create(context, "SHIFT_HANDOVER")).id}


@router.get("/ai/artifacts", response_model=list[ArtifactResponse])
async def artifacts(context: Context, service: Service, incident_id: str | None = None):
    async with service.database.sessions() as session:
        query = select(AIArtifact).where(
            AIArtifact.organization_id == context.organization_id
        )
        if incident_id:
            query = query.where(AIArtifact.incident_id == incident_id)
        return list(
            await session.scalars(
                query.order_by(AIArtifact.created_at.desc(), AIArtifact.id).limit(100)
            )
        )


@router.get("/ai/artifacts/{identifier}", response_model=ArtifactResponse)
async def artifact(identifier: str, context: Context, service: Service):
    async with service.database.sessions() as session:
        row = await session.scalar(
            select(AIArtifact).where(
                AIArtifact.organization_id == context.organization_id,
                AIArtifact.id == identifier,
            )
        )
        if row is None:
            raise HTTPException(404, "AI artifact was not found.")
        return row
