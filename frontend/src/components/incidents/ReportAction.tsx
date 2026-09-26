import { useState } from 'react'
import { Link } from 'react-router-dom'
import { client, apiErrorMessage } from '../../api/client'
import { useAuth } from '../../auth/context'
import { canOperate } from '../../auth/session'
export function ReportAction({ incidentId }: { incidentId: string }) {
  const { user } = useAuth()
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  if (!canOperate(user)) return null
  async function generate(ai = false) {
    setPending(true)
    setMessage(null)
    try {
      const response = await client.post<{ job_id: string }>(
        `/incidents/${incidentId}/${ai ? 'ai-summary' : 'reports'}`,
      )
      setMessage(
        `${ai ? 'AI summary' : 'Report'} queued: ${response.data.job_id}. View its progress in Jobs and reports.`,
      )
    } catch (e) {
      setMessage(apiErrorMessage(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <div className="rounded-xl bg-white p-5">
      <div className="flex flex-wrap gap-3">
        <button
          className="primary-button"
          disabled={pending}
          onClick={() => void generate()}
        >
          Generate operational report
        </button>
        <button
          className="rounded-lg border border-teal-700 px-4 py-2 text-sm font-medium text-teal-700 disabled:opacity-50"
          disabled={pending}
          onClick={() => void generate(true)}
        >
          Generate AI Summary
        </button>
      </div>
      <p className="mt-3 text-xs text-slate-500">
        AI-generated summaries: Human review required.
      </p>
      {message && (
        <p role="status" className="mt-3 text-sm">
          {message}
        </p>
      )}
      <Link to="/jobs" className="mt-3 block text-sm text-teal-700">
        Jobs and reports →
      </Link>
    </div>
  )
}
