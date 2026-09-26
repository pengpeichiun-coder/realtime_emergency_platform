import type { CreateIncidentInput } from '../types/domain'
import { client } from './client'
import { mapIncident, mapSnapshot } from './mappers'
import type { IncidentDto, DispatchDto, SnapshotDto, NearbyUnitDto } from './types'

export async function getOperations(signal?: AbortSignal) {
  return mapSnapshot((await client.get<SnapshotDto>('/state', { signal })).data)
}
export async function createIncident(input: CreateIncidentInput) {
  return mapIncident((await client.post<IncidentDto>('/incidents', input)).data)
}
export async function createDispatch(incidentId: string, unitId: string) {
  return (
    await client.post<DispatchDto>('/dispatches', {
      incident_id: incidentId,
      unit_id: unitId,
    })
  ).data
}
export async function resolveIncident(id: string) {
  return mapIncident(
    (
      await client.post<IncidentDto>(
        `/incidents/${encodeURIComponent(id)}/resolve`,
      )
    ).data,
  )
}
export async function resetDemo() {
  return mapSnapshot((await client.post<SnapshotDto>('/demo/reset')).data)
}

export async function getNearbyUnits(incidentId: string, signal?: AbortSignal) {
  return (await client.get<NearbyUnitDto[]>(
    `/incidents/${encodeURIComponent(incidentId)}/nearby-units`,
    { signal, params: { radius_meters: 10000, limit: 10 } },
  )).data
}
