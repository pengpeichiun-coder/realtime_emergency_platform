export type IncidentSeverity = 'low' | 'medium' | 'high' | 'critical'

export type IncidentStatus = 'active' | 'dispatched' | 'on_scene' | 'resolved'

export type UnitType = 'ambulance' | 'fire_truck' | 'police'

export type UnitStatus =
  'available' | 'dispatched' | 'en_route' | 'on_scene' | 'offline'

export type IncidentType = 'fire' | 'medical' | 'traffic' | 'security'

export interface Incident {
  id: string
  type: IncidentType
  title: string
  area: string
  severity: IncidentSeverity
  status: IncidentStatus
  latitude: number
  longitude: number
  description: string
  createdAt: string
  assignedUnitId?: string
  responseTimeSeconds?: number
}

export interface EmergencyUnit {
  id: string
  name: string
  type: UnitType
  status: UnitStatus
  area: string
  latitude: number
  longitude: number
  lastUpdatedAt: string
  assignedIncidentId?: string
}

export interface CreateIncidentInput {
  type: IncidentType
  title: string
  area: string
  severity: IncidentSeverity
  description: string
  latitude: number
  longitude: number
}

export type DispatchStatus = 'created' | 'en_route' | 'arrived' | 'completed'
export interface EmergencyDispatch {
  id: string
  incidentId: string
  unitId: string
  status: DispatchStatus
  createdAt: string
  startedAt: string | null
  arrivedAt: string | null
  completedAt: string | null
  origin: { latitude: number; longitude: number }
}

export interface ActivityEvent {
  id: string
  eventType: string
  entityType: string
  entityId: string
  message: string
  timestamp: string
}

export interface OperationsSnapshot {
  streamId: string
  sequence: number
  incidents: Incident[]
  units: EmergencyUnit[]
  dispatches: EmergencyDispatch[]
  activity: ActivityEvent[]
}
