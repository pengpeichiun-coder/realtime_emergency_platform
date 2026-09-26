import type {
  ActivityDto,
  DispatchDto,
  IncidentDto,
  SnapshotDto,
  UnitDto,
} from '../api/types.ts'
import {
  mapActivity,
  mapDispatch,
  mapIncident,
  mapSnapshot,
  mapUnit,
} from '../api/mappers.ts'
import type { OperationsSnapshot } from '../types/domain.ts'

interface EventEnvelope<Type extends string, Data> {
  type: Type
  timestamp: string
  stream_id: string
  sequence: number
  data: Data
}

export type ServerEvent =
  | EventEnvelope<
      'incident.created' | 'incident.updated' | 'incident.resolved',
      IncidentDto
    >
  | EventEnvelope<'unit.status.updated' | 'unit.location.updated', UnitDto>
  | EventEnvelope<
      'dispatch.created' | 'dispatch.updated' | 'dispatch.completed',
      DispatchDto
    >
  | EventEnvelope<'activity.created', ActivityDto>
  | EventEnvelope<'state.reset', SnapshotDto>
  | EventEnvelope<'connection.ready', Record<string, never>>

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function isSequence(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function isUtcTimestamp(value: unknown): value is string {
  if (
    typeof value !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value)
  ) return false

  const parsed = Date.parse(value)
  // Date.parse normalizes impossible calendar dates; reject that normalization.
  return Number.isFinite(parsed) &&
    new Date(parsed).toISOString().slice(0, 19) === value.slice(0, 19)
}

function isNullableText(value: unknown): value is string | null {
  return value === null || isText(value)
}

function isNullableTimestamp(value: unknown): value is string | null {
  return value === null || isUtcTimestamp(value)
}

function isCoordinate(value: unknown, bound: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) &&
    value >= -bound && value <= bound
}

function isEnum<Value extends string>(
  value: unknown,
  values: readonly Value[],
): value is Value {
  return typeof value === 'string' && values.some((candidate) => candidate === value)
}

function isIncident(value: unknown): value is IncidentDto {
  return isObject(value) &&
    isText(value.id) && isText(value.title) && isText(value.area) &&
    isText(value.description) &&
    isEnum(value.type, ['fire', 'medical', 'traffic', 'security']) &&
    isEnum(value.severity, ['low', 'medium', 'high', 'critical']) &&
    isEnum(value.status, ['active', 'dispatched', 'on_scene', 'resolved']) &&
    isCoordinate(value.latitude, 90) && isCoordinate(value.longitude, 180) &&
    isUtcTimestamp(value.created_at) && isNullableText(value.assigned_unit_id) &&
    (value.response_time_seconds === null || isSequence(value.response_time_seconds))
}

function isUnit(value: unknown): value is UnitDto {
  return isObject(value) &&
    isText(value.id) && isText(value.name) && isText(value.area) &&
    isEnum(value.type, ['ambulance', 'fire_truck', 'police']) &&
    isEnum(value.status, ['available', 'dispatched', 'en_route', 'on_scene', 'offline']) &&
    isCoordinate(value.latitude, 90) && isCoordinate(value.longitude, 180) &&
    isUtcTimestamp(value.last_updated_at) && isNullableText(value.assigned_incident_id)
}

function isDispatch(value: unknown): value is DispatchDto {
  return isObject(value) &&
    isText(value.id) && isText(value.incident_id) && isText(value.unit_id) &&
    isEnum(value.status, ['created', 'en_route', 'arrived', 'completed']) &&
    isUtcTimestamp(value.created_at) &&
    isNullableTimestamp(value.started_at) &&
    isNullableTimestamp(value.arrived_at) &&
    isNullableTimestamp(value.completed_at) &&
    isCoordinate(value.origin_latitude, 90) &&
    isCoordinate(value.origin_longitude, 180)
}

function isActivity(value: unknown): value is ActivityDto {
  return isObject(value) &&
    isText(value.id) && isText(value.event_type) && isText(value.entity_type) &&
    isText(value.entity_id) && isText(value.message) && isUtcTimestamp(value.timestamp)
}

function isCollection<Entity extends { id: string }>(
  value: unknown,
  validate: (candidate: unknown) => candidate is Entity,
): value is Entity[] {
  if (!Array.isArray(value)) return false
  const items: unknown[] = value
  return items.every(validate) &&
    new Set(items.map((item) => item.id)).size === items.length
}

function isSnapshot(value: unknown): value is SnapshotDto {
  return isObject(value) &&
    isText(value.stream_id) && isSequence(value.sequence) &&
    isCollection(value.incidents, isIncident) &&
    isCollection(value.units, isUnit) &&
    isCollection(value.dispatches, isDispatch) &&
    isCollection(value.activity, isActivity)
}

export function parseServerEvent(raw: string): ServerEvent {
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    throw new Error('Realtime message is not valid JSON.')
  }
  if (
    !isObject(value) || !isText(value.type) ||
    !isUtcTimestamp(value.timestamp) || !isText(value.stream_id) ||
    !isSequence(value.sequence) || !isObject(value.data)
  ) {
    throw new Error('Invalid realtime envelope: expected a type, UTC timestamp, stream ID, sequence, and object data.')
  }

  const envelope = {
    timestamp: value.timestamp,
    stream_id: value.stream_id,
    sequence: value.sequence,
  }
  switch (value.type) {
    case 'incident.created':
    case 'incident.updated':
    case 'incident.resolved':
      if (isIncident(value.data)) return { ...envelope, type: value.type, data: value.data }
      break
    case 'unit.status.updated':
    case 'unit.location.updated':
      if (isUnit(value.data)) return { ...envelope, type: value.type, data: value.data }
      break
    case 'dispatch.created':
    case 'dispatch.updated':
    case 'dispatch.completed':
      if (isDispatch(value.data)) return { ...envelope, type: value.type, data: value.data }
      break
    case 'activity.created':
      if (isActivity(value.data)) return { ...envelope, type: value.type, data: value.data }
      break
    case 'state.reset':
      if (
        isSnapshot(value.data) &&
        value.data.stream_id === value.stream_id &&
        value.data.sequence === value.sequence
      ) return { ...envelope, type: value.type, data: value.data }
      break
    case 'connection.ready':
      if (Object.keys(value.data).length === 0)
        return { ...envelope, type: value.type, data: {} }
      break
    default:
      throw new Error(`Unsupported realtime event type: ${value.type}.`)
  }
  throw new Error(`Invalid payload for realtime event ${value.type}.`)
}

export class ResyncRequiredError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ResyncRequiredError'
  }
}

function upsert<Entity extends { id: string }>(items: Entity[], entity: Entity): Entity[] {
  return items.some((item) => item.id === entity.id)
    ? items.map((item) => item.id === entity.id ? entity : item)
    : [entity, ...items]
}

/** Apply server state only; the browser never infers movement or transitions. */
export function applyServerEvent(
  snapshot: OperationsSnapshot,
  event: ServerEvent,
): OperationsSnapshot {
  if (event.type === 'connection.ready') return snapshot
  if (event.stream_id !== snapshot.streamId) {
    throw new ResyncRequiredError('The server stream changed. Reload authoritative state.')
  }
  if (event.sequence <= snapshot.sequence) return snapshot

  // A reset carries a complete snapshot, so it can safely repair a sequence gap.
  if (event.type === 'state.reset') return mapSnapshot(event.data)
  if (event.sequence !== snapshot.sequence + 1) {
    throw new ResyncRequiredError('Realtime events were missed. Reload authoritative state.')
  }

  const next = { ...snapshot, sequence: event.sequence }
  switch (event.type) {
    case 'incident.created':
    case 'incident.updated':
    case 'incident.resolved':
      return { ...next, incidents: upsert(snapshot.incidents, mapIncident(event.data)) }
    case 'unit.status.updated':
    case 'unit.location.updated':
      return { ...next, units: upsert(snapshot.units, mapUnit(event.data)) }
    case 'dispatch.created':
    case 'dispatch.updated':
    case 'dispatch.completed':
      return { ...next, dispatches: upsert(snapshot.dispatches, mapDispatch(event.data)) }
    case 'activity.created':
      return {
        ...next,
        activity: [
          mapActivity(event.data),
          ...snapshot.activity.filter((item) => item.id !== event.data.id),
        ].slice(0, 100),
      }
  }
}
