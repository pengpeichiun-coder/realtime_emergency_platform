/// <reference types="node" />
import assert from 'node:assert/strict'
import test from 'node:test'
import type { ActivityDto, DispatchDto, IncidentDto, SnapshotDto, UnitDto } from '../api/types.ts'
import { mapSnapshot } from '../api/mappers.ts'
import {
  applyServerEvent,
  parseServerEvent,
  ResyncRequiredError,
} from './events.ts'
import type { ServerEvent } from './events.ts'

const timestamp = '2026-09-22T10:30:00.123456Z'
const streamId = 'server-test-session'
const incident: IncidentDto = {
  id: 'INC-001', type: 'fire', title: 'Test fire', area: 'Marina Bay',
  severity: 'high', status: 'active', latitude: 1.2834, longitude: 103.8607,
  description: 'Fictional test incident', created_at: timestamp,
  assigned_unit_id: null, response_time_seconds: null,
}
const unit: UnitDto = {
  id: 'FIRE-001', name: 'Test fire engine', type: 'fire_truck',
  status: 'available', area: 'Central', latitude: 1.3, longitude: 103.84,
  last_updated_at: timestamp, assigned_incident_id: null,
}
const dispatch: DispatchDto = {
  id: 'DSP-001', incident_id: incident.id, unit_id: unit.id,
  status: 'created', created_at: timestamp,
  started_at: null, arrived_at: null, completed_at: null,
  origin_latitude: unit.latitude, origin_longitude: unit.longitude,
}
const activity: ActivityDto = {
  id: 'ACT-001', event_type: 'dispatch.created', entity_type: 'dispatch',
  entity_id: dispatch.id, message: 'Unit dispatched.', timestamp,
}

function snapshotDto(sequence = 0): SnapshotDto {
  return {
    incidents: [{ ...incident }], units: [{ ...unit }],
    dispatches: [], activity: [], stream_id: streamId, sequence,
  }
}

function envelope(type: string, data: unknown, sequence = 1, stream = streamId) {
  return { type, timestamp, stream_id: stream, sequence, data }
}

function event(type: ServerEvent['type'], data: unknown, sequence = 1, stream = streamId) {
  return parseServerEvent(JSON.stringify(envelope(type, data, sequence, stream)))
}

test('decodes every event family with its complete typed payload', () => {
  const examples: [ServerEvent['type'], unknown][] = [
    ['incident.created', incident], ['incident.updated', incident],
    ['incident.resolved', { ...incident, status: 'resolved' }],
    ['unit.status.updated', unit], ['unit.location.updated', unit],
    ['dispatch.created', dispatch], ['dispatch.updated', dispatch],
    ['dispatch.completed', { ...dispatch, status: 'completed', completed_at: timestamp }],
    ['activity.created', activity], ['state.reset', snapshotDto(1)],
    ['connection.ready', {}],
  ]
  for (const [type, data] of examples) {
    assert.deepEqual(event(type, data), envelope(type, data))
  }
})

test('accepts UTC timestamps with microseconds and explicit zero offset', () => {
  assert.equal(event('incident.created', incident).timestamp, timestamp)
  const value = envelope('connection.ready', {})
  value.timestamp = '2026-09-22T10:30:00+00:00'
  assert.equal(parseServerEvent(JSON.stringify(value)).timestamp, value.timestamp)
})

test('rejects malformed JSON and invalid or unsupported envelopes', () => {
  assert.throws(() => parseServerEvent('{broken'), /valid JSON/)
  for (const value of [
    null, [], {},
    { ...envelope('connection.ready', {}), sequence: -1 },
    { ...envelope('connection.ready', {}), sequence: 0.5 },
    { ...envelope('connection.ready', {}), sequence: Number.MAX_SAFE_INTEGER + 1 },
    { ...envelope('connection.ready', {}), sequence: '1' },
    { ...envelope('connection.ready', {}), stream_id: ' ' },
    envelope('connection.ready', null),
    envelope('connection.ready', []),
    envelope('connection.ready', { unexpected: true }),
    envelope('unknown.event', {}),
  ]) assert.throws(() => parseServerEvent(JSON.stringify(value)), /realtime/)
})

test('rejects non-UTC, malformed, and impossible calendar timestamps', () => {
  for (const invalid of [
    'not-a-date', '2026-09-22', '2026-09-22T10:30:00',
    '2026-09-22T18:30:00+08:00', '2026-02-30T10:30:00Z',
    '2026-13-22T10:30:00Z', '2026-09-22T24:00:00Z',
  ]) {
    assert.throws(() => parseServerEvent(JSON.stringify({
      ...envelope('connection.ready', {}), timestamp: invalid,
    })), /envelope/)
    assert.throws(() => event('incident.created', { ...incident, created_at: invalid }), /payload/)
  }
})

test('rejects invalid incident statuses, enums, coordinates, and missing nullable fields', () => {
  for (const patch of [
    { status: 'en_route' }, { severity: 'urgent' }, { type: 'unknown' },
    { latitude: 91 }, { longitude: -181 }, { latitude: '1.3' },
    { latitude: Number.NaN }, { longitude: Number.POSITIVE_INFINITY },
    { id: '' }, { title: ' ' }, { description: null },
    { assigned_unit_id: undefined }, { response_time_seconds: undefined },
    { response_time_seconds: -1 }, { response_time_seconds: 1.5 },
  ]) assert.throws(() => event('incident.created', { ...incident, ...patch }), /payload/)
})

test('rejects malformed units before corrupt location or status can reach the UI', () => {
  for (const patch of [
    { status: 'arrived' }, { type: 'helicopter' }, { name: null },
    { latitude: -91 }, { longitude: 181 }, { longitude: null },
    { last_updated_at: 'today' }, { assigned_incident_id: undefined },
  ]) assert.throws(() => event('unit.location.updated', { ...unit, ...patch }), /payload/)
})

test('requires valid full dispatch and activity payloads', () => {
  for (const patch of [
    { status: 'dispatched' }, { incident_id: null }, { unit_id: '' },
    { started_at: undefined }, { arrived_at: 'yesterday' },
    { completed_at: 123 }, { origin_latitude: 91 },
  ]) assert.throws(() => event('dispatch.updated', { ...dispatch, ...patch }), /payload/)
  for (const patch of [
    { entity_id: null }, { message: '' }, { event_type: 2 }, { timestamp: 'today' },
  ]) assert.throws(() => event('activity.created', { ...activity, ...patch }), /payload/)
})

test('reset validates all collections, unique IDs, and matching envelope metadata', () => {
  for (const patch of [
    { stream_id: 'other-server' }, { sequence: 2 }, { incidents: undefined },
    { units: [{ ...unit, status: 'broken' }] }, { dispatches: [{}] },
    { activity: [null] }, { incidents: [incident, incident] },
  ]) assert.throws(() => event('state.reset', { ...snapshotDto(1), ...patch }), /payload/)
})

test('incident create and update upsert server fields without changing other entities', () => {
  const before = mapSnapshot(snapshotDto())
  const added = { ...incident, id: 'INC-002', title: 'New report' }
  const created = applyServerEvent(before, event('incident.created', added))
  assert.equal(created.incidents.length, 2)
  assert.equal(created.incidents[0].id, added.id)
  assert.equal(created.units, before.units)
  assert.equal(before.incidents.length, 1)
  const updated = applyServerEvent(created, event('incident.updated', {
    ...added, severity: 'critical', title: 'Updated report',
  }, 2))
  assert.equal(updated.incidents.length, 2)
  assert.equal(updated.incidents[0].title, 'Updated report')
  assert.equal(updated.incidents[0].severity, 'critical')
  assert.equal(updated.sequence, 2)
})

test('unit location is copied exactly from the server without interpolation or arrival inference', () => {
  const before = mapSnapshot(snapshotDto())
  const location = {
    ...unit, status: 'en_route', assigned_incident_id: incident.id,
    latitude: incident.latitude, longitude: incident.longitude,
  }
  const after = applyServerEvent(before, event('unit.location.updated', location))
  assert.equal(after.units[0].latitude, incident.latitude)
  assert.equal(after.units[0].longitude, incident.longitude)
  assert.equal(after.units[0].status, 'en_route')
  assert.equal(after.incidents, before.incidents)
  assert.equal(after.incidents[0].status, 'active')
  assert.equal(before.units[0].latitude, unit.latitude)
})

test('unit status is server-authored and does not infer an incident transition', () => {
  const before = mapSnapshot(snapshotDto())
  const after = applyServerEvent(before, event('unit.status.updated', {
    ...unit, status: 'on_scene', assigned_incident_id: incident.id,
  }))
  assert.equal(after.units[0].status, 'on_scene')
  assert.equal(after.units[0].assignedIncidentId, incident.id)
  assert.equal(after.incidents, before.incidents)
})

test('dispatch create, update, and completion retain authoritative timing and identity', () => {
  const initial = mapSnapshot(snapshotDto())
  const created = applyServerEvent(initial, event('dispatch.created', dispatch))
  const started = applyServerEvent(created, event('dispatch.updated', {
    ...dispatch, status: 'en_route', started_at: timestamp,
  }, 2))
  const completed = applyServerEvent(started, event('dispatch.completed', {
    ...dispatch, status: 'completed', started_at: timestamp,
    arrived_at: timestamp, completed_at: timestamp,
  }, 3))
  assert.equal(completed.dispatches.length, 1)
  assert.equal(completed.dispatches[0].status, 'completed')
  assert.equal(completed.dispatches[0].completedAt, timestamp)
  assert.deepEqual(completed.dispatches[0].origin, {
    latitude: unit.latitude, longitude: unit.longitude,
  })
  assert.equal(completed.units, initial.units)
})

test('incident resolution uses complete server fields and waits for separate unit updates', () => {
  const initial = snapshotDto()
  initial.incidents[0] = { ...incident, status: 'on_scene', assigned_unit_id: unit.id }
  initial.units[0] = { ...unit, status: 'on_scene', assigned_incident_id: incident.id }
  const before = mapSnapshot(initial)
  const resolved = applyServerEvent(before, event('incident.resolved', {
    ...incident, status: 'resolved', assigned_unit_id: null, response_time_seconds: 19,
  }))
  assert.equal(resolved.incidents[0].status, 'resolved')
  assert.equal(resolved.incidents[0].assignedUnitId, undefined)
  assert.equal(resolved.incidents[0].responseTimeSeconds, 19)
  assert.equal(resolved.units, before.units)
  assert.equal(resolved.units[0].status, 'on_scene')
})

test('duplicate and older events preserve object identity and never rewind locations', () => {
  const before = mapSnapshot(snapshotDto(5))
  for (const sequence of [0, 4, 5]) {
    assert.equal(applyServerEvent(before, event('unit.location.updated', {
      ...unit, latitude: 50,
    }, sequence)), before)
  }
})

test('sequence gaps require REST resynchronization without partially applying the event', () => {
  const before = mapSnapshot(snapshotDto(3))
  assert.throws(() => applyServerEvent(before, event('incident.updated', {
    ...incident, title: 'Should not apply',
  }, 5)), ResyncRequiredError)
  assert.equal(before.sequence, 3)
  assert.equal(before.incidents[0].title, incident.title)
})

test('a server stream restart requires resynchronization even when its sequence is older', () => {
  const before = mapSnapshot(snapshotDto(9))
  assert.throws(() => applyServerEvent(before, event(
    'unit.status.updated', unit, 1, 'restarted-server',
  )), ResyncRequiredError)
  assert.throws(() => applyServerEvent(before, event('state.reset', {
    ...snapshotDto(1), stream_id: 'restarted-server',
  }, 1, 'restarted-server')), ResyncRequiredError)
})

test('connection readiness is handled by the connection client and does not advance state', () => {
  const before = mapSnapshot(snapshotDto())
  assert.equal(applyServerEvent(before, event('connection.ready', {}, 20, 'other')), before)
})

test('activity is latest first, deduplicated by ID, and capped at 100 entries', () => {
  let state = mapSnapshot(snapshotDto())
  for (let sequence = 1; sequence <= 105; sequence += 1) {
    state = applyServerEvent(state, event('activity.created', {
      ...activity, id: `ACT-${sequence}`,
    }, sequence))
  }
  assert.equal(state.activity.length, 100)
  assert.equal(state.activity[0].id, 'ACT-105')
  assert.equal(state.activity[99].id, 'ACT-6')
  state = applyServerEvent(state, event('activity.created', {
    ...activity, id: 'ACT-100', message: 'Updated authoritative message.',
  }, 106))
  assert.equal(state.activity.length, 100)
  assert.equal(state.activity[0].id, 'ACT-100')
  assert.equal(state.activity[0].message, 'Updated authoritative message.')
  assert.equal(state.activity.filter((item) => item.id === 'ACT-100').length, 1)
})

test('a newer full reset replaces every collection and can repair an event gap', () => {
  const before = mapSnapshot({
    ...snapshotDto(7), dispatches: [dispatch], activity: [activity],
  })
  const reset = {
    ...snapshotDto(20), units: [], activity: [], dispatches: [],
    incidents: [{ ...incident, id: 'INC-RESET' }],
  }
  const after = applyServerEvent(before, event('state.reset', reset, 20))
  assert.deepEqual(after, mapSnapshot(reset))
  assert.equal(before.dispatches.length, 1)
})

test('an old or repeated full reset cannot overwrite newer server state', () => {
  const before = mapSnapshot(snapshotDto(8))
  for (const sequence of [3, 8]) {
    const reset = { ...snapshotDto(sequence), incidents: [], units: [] }
    assert.equal(applyServerEvent(before, event('state.reset', reset, sequence)), before)
  }
})
