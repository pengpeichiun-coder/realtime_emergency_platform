import type { ReactNode } from 'react'
import { useOperations } from './context'

export function OperationsGate({ children }: { children: ReactNode }) {
  const { ready, loading, error, refresh, connection } = useOperations()
  return (
    <>
      {ready && connection !== 'live' && (
        <p
          role="status"
          className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800"
        >
          {connection === 'syncing'
            ? 'Synchronizing authoritative state…'
            : 'Realtime connection lost. Reconnecting automatically; displayed data may be stale.'}
        </p>
      )}
      {error && (
        <div
          role="alert"
          className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
        >
          <span>
            {error}
            {ready ? ' Displaying the last received state.' : ''}
          </span>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={connection !== 'live'}
            className="rounded border border-red-200 px-3 py-1 font-semibold"
          >
            Refresh state
          </button>
        </div>
      )}
      {!ready ? (
        <p
          role="status"
          className="rounded-xl border border-slate-200 bg-white p-8 text-sm text-slate-600"
        >
          {loading
            ? 'Loading operations from FastAPI…'
            : 'Operations are unavailable until the API is reachable.'}
        </p>
      ) : (
        children
      )}
    </>
  )
}
