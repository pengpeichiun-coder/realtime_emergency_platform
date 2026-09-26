import type {
  ActivityEvent,
  EmergencyDispatch,
  EmergencyUnit,
  Incident,
  OperationsSnapshot,
} from '../types/domain.ts'
import type {
  ActivityDto,
  DispatchDto,
  IncidentDto,
  SnapshotDto,
  UnitDto,
} from './types.ts'

export function mapIncident(dto: IncidentDto): Incident {
  const { created_at, assigned_unit_id, response_time_seconds, ...fields } = dto
  return {
    ...fields,
    createdAt: created_at,
    assignedUnitId: assigned_unit_id ?? undefined,
    responseTimeSeconds: response_time_seconds ?? undefined,
  }
}
export function mapUnit(dto: UnitDto): EmergencyUnit {
  const { last_updated_at, assigned_incident_id, ...fields } = dto
  return {
    ...fields,
    lastUpdatedAt: last_updated_at,
    assignedIncidentId: assigned_incident_id ?? undefined,
  }
}
export function mapDispatch(dto: DispatchDto): EmergencyDispatch {
  return {
    id: dto.id,
    incidentId: dto.incident_id,
    unitId: dto.unit_id,
    status: dto.status,
    createdAt: dto.created_at,
    startedAt: dto.started_at,
    arrivedAt: dto.arrived_at,
    completedAt: dto.completed_at,
    origin: { latitude: dto.origin_latitude, longitude: dto.origin_longitude },
  }
}
export function mapActivity(dto: ActivityDto): ActivityEvent {
  return {
    id: dto.id,
    eventType: dto.event_type,
    entityType: dto.entity_type,
    entityId: dto.entity_id,
    message: dto.message,
    timestamp: dto.timestamp,
  }
}
export function mapSnapshot(dto: SnapshotDto): OperationsSnapshot {
  return {
    streamId: dto.stream_id,
    sequence: dto.sequence,
    incidents: dto.incidents.map(mapIncident),
    units: dto.units.map(mapUnit),
    dispatches: dto.dispatches.map(mapDispatch),
    activity: dto.activity.map(mapActivity),
  }
}
