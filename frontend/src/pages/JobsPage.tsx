import { useEffect, useState } from 'react'
import { client, apiErrorMessage } from '../api/client'
import { useAuth } from '../auth/context'
import { canOperate } from '../auth/session'
import { ArtifactView } from '../components/ai/ArtifactView'
export interface Job {
  id: string
  job_type: string
  status: string
  attempt_count: number
  error_message: string | null
  result_payload: Record<string, unknown> | null
  created_at: string
}
export function JobsPage() {
  const { user } = useAuth()
  const [jobs, setJobs] = useState<Job[]>([])
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function load() {
      try {
        const response = await client.get<Job[]>('/jobs', {
          signal: controller.signal,
        })
        setJobs(response.data)
        setError(null)
      } catch (e) {
        if (!controller.signal.aborted) setError(apiErrorMessage(e))
      }
      if (!controller.signal.aborted)
        timer = setTimeout(() => void load(), 2000)
    }
    void load()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [])
  async function handover() {
    setPending(true)
    setError(null)
    setNotice(null)
    try {
      const response = await client.post<{ job_id: string }>(
        '/ai/shift-handover',
      )
      setNotice(`Shift handover queued: ${response.data.job_id}`)
    } catch (e) {
      setError(apiErrorMessage(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <>
      <p className="kicker">BACKGROUND WORK</p>
      <h1 className="page-title">Jobs and reports</h1>
      {canOperate(user) && (
        <button
          className="primary-button mt-5"
          disabled={pending}
          onClick={() => void handover()}
        >
          Generate Shift Handover
        </button>
      )}
      <p className="mt-3 text-sm text-slate-500">
        AI-generated summaries are advisory. Human review required. Latest 100
        jobs shown.
      </p>
      {notice && (
        <p role="status" className="mt-3 text-sm">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="my-4 text-red-700">
          {error}
        </p>
      )}
      {!jobs.length && (
        <p className="mt-6 text-slate-500">
          No jobs yet. Generate a report from the incident overview.
        </p>
      )}
      <ul aria-label="Background jobs" className="mt-6 space-y-4">
        {jobs.map((job) => {
          const ai = ['INCIDENT_SUMMARY', 'SHIFT_HANDOVER'].includes(
            job.job_type,
          )
          return (
            <li key={job.id} className="rounded-xl bg-white p-5">
              <p className="font-semibold">
                {job.job_type.replaceAll('_', ' ')}
              </p>
              <p className="mt-1 text-sm">
                {job.status.charAt(0) + job.status.slice(1).toLowerCase()} ·
                Attempts: {job.attempt_count}
              </p>
              <p className="text-xs text-slate-500">
                {new Date(job.created_at).toLocaleString()} · {job.id}
              </p>
              {job.error_message && (
                <p className="mt-2 text-sm text-amber-800">
                  {job.error_message}
                </p>
              )}
              {job.result_payload && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-teal-700">
                    {ai ? 'View AI summary' : 'View report'}
                  </summary>
                  {ai ? (
                    <ArtifactView result={job.result_payload} />
                  ) : (
                    <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">
                      {JSON.stringify(job.result_payload, null, 2)}
                    </pre>
                  )}
                </details>
              )}
            </li>
          )
        })}
      </ul>
    </>
  )
}
