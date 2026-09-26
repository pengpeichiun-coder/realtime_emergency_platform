import { useEffect, useState } from 'react'
import { getNearbyUnits } from '../../api/operations'
import { apiErrorMessage } from '../../api/client'
import type { NearbyUnitDto } from '../../api/types'
import type { EmergencyUnit, Incident } from '../../types/domain'

interface Result {
  key: string
  units: NearbyUnitDto[]
  error: string | null
}

export function useNearbyUnits(incident: Incident | undefined, units: EmergencyUnit[], enabled: boolean) {
  const [result, setResult] = useState<Result | null>(null)
  const [retry, setRetry] = useState(0)
  // Changes to availability/incident location invalidate distances; movement frames
  // for unavailable units do not generate a REST request every 200 ms.
  const availability = units.map(unit => {
    const location = unit.status === 'available' ? `${unit.latitude},${unit.longitude}` : ''
    return `${unit.id}:${unit.status}:${unit.assignedIncidentId ?? ''}:${location}`
  }).join('|')
  const id = enabled && incident?.status === 'active' ? incident.id : null
  const key = `${id}:${incident?.latitude}:${incident?.longitude}:${availability}:${retry}`

  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    void getNearbyUnits(id, controller.signal).then(
      data => {
        if (!controller.signal.aborted) setResult({ key, units: data, error: null })
      },
      error => {
        if (!controller.signal.aborted) setResult({ key, units: [], error: apiErrorMessage(error) })
      },
    )
    return () => controller.abort()
  }, [id, key])

  const current = result?.key === key ? result : null
  return {
    units: current?.units ?? [],
    loading: Boolean(id && !current),
    error: current?.error ?? null,
    retry: () => setRetry(value => value + 1),
  }
}
