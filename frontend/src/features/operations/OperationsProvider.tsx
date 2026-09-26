import { authSession } from '../../auth/runtime'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type {
  CreateIncidentInput,
  OperationsSnapshot,
} from '../../types/domain'
import * as api from '../../api/operations'
import { API_URL, apiErrorMessage } from '../../api/client'
import { OperationsStream } from '../../realtime/connection'
import type { ConnectionStatus } from '../../realtime/connection'
import { OperationsContext } from './context'

const empty: OperationsSnapshot = {
  streamId: '',
  sequence: 0,
  incidents: [],
  units: [],
  dispatches: [],
  activity: [],
}

export function OperationsProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<OperationsSnapshot>(empty)
  const [ready, setReady] = useState(false)
  const [connection, setConnection] = useState<ConnectionStatus>('connecting')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedIncidentId, selectIncident] = useState<string | null>(null)
  const mounted = useRef(false)
  const busy = useRef(false)
  const stream = useRef<OperationsStream | null>(null)

  useEffect(() => {
    mounted.current = true
    const client = new OperationsStream({
      url: `${API_URL.replace(/^http/, 'ws')}/ws/events`,
      authenticate: () => authSession.refresh(),
      fetchSnapshot: api.getOperations,
      onSnapshot: (data) => {
        if (!mounted.current) return
        setSnapshot(data)
        setReady(true)
        selectIncident((id) =>
          data.incidents.some((item) => item.id === id) ? id : null,
        )
      },
      onStatus: (status) => {
        if (mounted.current) setConnection(status)
      },
      onError: (message) => {
        if (mounted.current) setError(message)
      },
      onReset: () => selectIncident(null),
    })
    stream.current = client
    queueMicrotask(() => client.start())
    return () => {
      mounted.current = false
      client.stop()
      stream.current = null
    }
  }, [])

  const refresh = useCallback(async () => {
    await stream.current?.refresh()
  }, [])

  async function command(
    operation: () => Promise<unknown>,
    selected?: string | null,
  ) {
    if (busy.current || connection !== 'live') return false
    busy.current = true
    setPending(true)
    setError(null)
    try {
      await operation()
      if (!mounted.current) return false
      await stream.current?.refresh()
      if (selected !== undefined && mounted.current) selectIncident(selected)
      return true
    } catch (cause) {
      if (mounted.current) {
        await stream.current?.refresh()
        setError(apiErrorMessage(cause))
      }
      return false
    } finally {
      busy.current = false
      if (mounted.current) setPending(false)
    }
  }

  async function createIncident(input: CreateIncidentInput) {
    return command(async () => {
      const incident = await api.createIncident(input)
      if (mounted.current) selectIncident(incident.id)
    })
  }

  return (
    <OperationsContext.Provider
      value={{
        snapshot,
        ready,
        loading: connection === 'connecting' || connection === 'syncing',
        pending,
        error,
        connection,
        selectedIncidentId,
        selectIncident,
        refresh,
        createIncident,
        dispatchUnit: (incidentId, unitId) =>
          command(() => api.createDispatch(incidentId, unitId)),
        resolveIncident: (id) => command(() => api.resolveIncident(id)),
        resetDemo: () => command(api.resetDemo, null),
      }}
    >
      {children}
    </OperationsContext.Provider>
  )
}
