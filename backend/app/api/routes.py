"""HTTP adaptation only; operational rules and locking live in the service."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.schemas.operations import (
    NearbyUnitResponse, ActivityResponse, DispatchCreate, DispatchResponse, IncidentCreate,
    IncidentPatch, IncidentResponse, StateResponse, UnitResponse,
)
from app.auth.policies import operator, development_admin
from app.services.operations import OperationsService
from app.context import RequestContext, request_context

router = APIRouter(prefix="/api")


async def service_for(request: Request, context: Annotated[RequestContext, Depends(request_context)]) -> OperationsService:
    if request.app.state.service is not None:
        if context.organization_id != request.app.state.trusted_context.organization_id:
            from fastapi import HTTPException
            raise HTTPException(404, "Organization was not found.")
        return request.app.state.service
    return await request.app.state.tenants.get(context.organization_id)


Service = Annotated[OperationsService, Depends(service_for)]


@router.get("/state", response_model=StateResponse, tags=["state"])
async def state(service: Service):
    return await service.snapshot()


@router.get("/incidents", response_model=list[IncidentResponse], tags=["incidents"])
async def incidents(service: Service):
    return (await service.snapshot())["incidents"]


@router.get("/incidents/{incident_id}", response_model=IncidentResponse, tags=["incidents"])
async def incident(incident_id: str, service: Service):
    return await service.get_incident(incident_id)


@router.post("/incidents", response_model=IncidentResponse, status_code=201, tags=["incidents"], dependencies=[Depends(operator)])
async def create_incident(body: IncidentCreate, service: Service):
    return await service.create_incident(body)


@router.patch("/incidents/{incident_id}", response_model=IncidentResponse, tags=["incidents"], dependencies=[Depends(operator)])
async def update_incident(incident_id: str, body: IncidentPatch, service: Service):
    return await service.update_incident(incident_id, body)


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentResponse, tags=["incidents"], dependencies=[Depends(operator)])
async def resolve_incident(incident_id: str, service: Service):
    return await service.resolve_incident(incident_id)


@router.get("/units", response_model=list[UnitResponse], tags=["units"])
async def units(service: Service):
    return (await service.snapshot())["units"]


@router.get("/units/{unit_id}", response_model=UnitResponse, tags=["units"])
async def unit(unit_id: str, service: Service):
    return await service.get_unit(unit_id)


@router.get("/dispatches", response_model=list[DispatchResponse], tags=["dispatches"])
async def dispatches(service: Service):
    return (await service.snapshot())["dispatches"]


@router.get("/dispatches/{dispatch_id}", response_model=DispatchResponse, tags=["dispatches"])
async def dispatch(dispatch_id: str, service: Service):
    return await service.get_dispatch(dispatch_id)


@router.post("/dispatches", response_model=DispatchResponse, status_code=201, tags=["dispatches"], dependencies=[Depends(operator)])
async def create_dispatch(body: DispatchCreate, service: Service):
    return await service.create_dispatch(body)


@router.get("/activity", response_model=list[ActivityResponse], tags=["activity"])
async def activity(service: Service):
    return (await service.snapshot())["activity"]


@router.post("/demo/reset", response_model=StateResponse, tags=["demo"], dependencies=[Depends(development_admin)])
async def reset_demo(service: Service):
    """Restore fictional seed data. Temporary development/demo endpoint."""
    return await service.reset()


@router.get('/incidents/{incident_id}/nearby-units', response_model=list[NearbyUnitResponse], tags=['incidents'])
async def nearby_units(incident_id: str, service: Service,
                       radius_meters: Annotated[float, Query(gt=0, le=50000)] = 10000,
                       limit: Annotated[int, Query(ge=1, le=50)] = 10):
    return await service.nearby_units(incident_id, radius_meters, limit)
