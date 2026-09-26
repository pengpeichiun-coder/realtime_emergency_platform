import { createContext, useContext } from 'react'
import type { ConnectionStatus } from '../../realtime/connection'
import type {
  CreateIncidentInput,
  OperationsSnapshot,
} from '../../types/domain'

export interface OperationsContextValue {
  snapshot: OperationsSnapshot
  ready: boolean
  loading: boolean
  pending: boolean
  connection: ConnectionStatus
  error: string | null
  selectedIncidentId: string | null
  selectIncident: (id: string) => void
  refresh: () => Promise<void>
  dispatchUnit: (incidentId: string, unitId: string) => Promise<boolean>
  createIncident: (input: CreateIncidentInput) => Promise<boolean>
  resolveIncident: (id: string) => Promise<boolean>
  resetDemo: () => Promise<boolean>
}
export const OperationsContext = createContext<OperationsContextValue | null>(
  null,
)
export function useOperations() {
  const value = useContext(OperationsContext)
  if (!value)
    throw new Error('useOperations must be used within OperationsProvider')
  return value
}
