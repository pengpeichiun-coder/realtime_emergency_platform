import type {
  Incident,
  EmergencyUnit,
  DispatchStatus,
} from '../types/domain.ts'

export interface IncidentDto extends Omit<
  Incident,
  'createdAt' | 'assignedUnitId' | 'responseTimeSeconds'
> {
  created_at: string
  assigned_unit_id: string | null
  response_time_seconds: number | null
}
export interface UnitDto extends Omit<
  EmergencyUnit,
  'lastUpdatedAt' | 'assignedIncidentId'
> {
  last_updated_at: string
  assigned_incident_id: string | null
}
export interface DispatchDto {
  id: string
  incident_id: string
  unit_id: string
  status: DispatchStatus
  created_at: string
  started_at: string | null
  arrived_at: string | null
  completed_at: string | null
  origin_latitude: number
  origin_longitude: number
}
export interface ActivityDto {
  id: string
  event_type: string
  entity_type: string
  entity_id: string
  message: string
  timestamp: string
}
export interface SnapshotDto {
  stream_id: string
  sequence: number
  incidents: IncidentDto[]
  units: UnitDto[]
  dispatches: DispatchDto[]
  activity: ActivityDto[]
}

export interface NearbyUnitDto {
  unit_id: string
  distance_meters: number
}
