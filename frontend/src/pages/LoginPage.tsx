import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/context'
import { authSession } from '../auth/runtime'
import { apiErrorMessage } from '../api/client'

export function LoginPage() {
  const { status, notice } = useAuth()
  const [register, setRegister] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (status === 'authenticated') return <Navigate to="/dashboard" replace />
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPending(true)
    setError(null)
    const fields = Object.fromEntries(
      new FormData(event.currentTarget),
    ) as Record<string, string>
    try {
      await authSession.signIn(fields, register)
    } catch (error) {
      setError(apiErrorMessage(error))
    } finally {
      setPending(false)
    }
  }
  return (
    <main className="grid min-h-screen bg-[#f5f6f8] lg:grid-cols-2">
      <section className="flex flex-col justify-between bg-[#132a34] p-8 text-white sm:p-14">
        <p className="text-lg font-bold tracking-[0.16em]">+ COMMAND</p>
        <div className="py-14">
          <p className="mb-5 text-xs tracking-[0.2em] text-teal-300">
            EMERGENCY OPERATIONS
          </p>
          <h1 className="max-w-lg text-4xl font-semibold leading-tight sm:text-5xl">
            A coordinated response.
            <br />
            <span className="text-slate-400">When it matters most.</span>
          </h1>
          <p className="mt-6 max-w-md text-sm leading-7 text-slate-300">
            One workspace to monitor incidents, locate response units, and
            coordinate emergency operations.
          </p>
        </div>
        <p className="text-xs text-slate-400">
          Real-Time Emergency Response Command Platform
        </p>
      </section>
      <section className="flex items-center justify-center p-8 sm:p-14">
        <div className="w-full max-w-sm">
          <p className="kicker">YOUR ORGANIZATION WORKSPACE</p>
          <h2 className="mt-3 text-3xl font-semibold text-slate-800">
            {register ? 'Create your organization' : 'Welcome to Command'}
          </h2>
          <p className="mb-8 mt-3 text-sm text-slate-500">
            {register
              ? 'Start a separate workspace as its first administrator.'
              : 'Sign in with your organization account.'}
          </p>
          {notice && (
            <p role="alert" className="mb-4 text-sm text-amber-800">
              {notice}
            </p>
          )}
          <form onSubmit={submit} className="space-y-5">
            {register && (
              <>
                <label className="block text-sm">
                  Organization name
                  <input
                    name="organization_name"
                    required
                    maxLength={120}
                    className="form-input mt-2"
                  />
                </label>
                <label className="block text-sm">
                  Display name
                  <input
                    name="display_name"
                    required
                    maxLength={120}
                    autoComplete="name"
                    className="form-input mt-2"
                  />
                </label>
              </>
            )}
            <label className="block text-sm">
              Email
              <input
                name="email"
                type="email"
                required
                autoComplete="username"
                className="form-input mt-2"
              />
            </label>
            <label className="block text-sm">
              Password
              <input
                name="password"
                type="password"
                required
                minLength={12}
                maxLength={128}
                autoComplete={register ? 'new-password' : 'current-password'}
                className="form-input mt-2"
              />
            </label>
            {register && (
              <p className="text-xs text-slate-500">Use 12–128 characters.</p>
            )}
            {error && (
              <p role="alert" className="text-sm text-red-700">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={pending || status === 'loading'}
              className="primary-button w-full justify-center"
            >
              {pending
                ? 'Please wait…'
                : register
                  ? 'Create organization'
                  : 'Sign in'}
            </button>
          </form>
          <button
            className="mt-6 text-sm font-medium text-teal-700"
            disabled={pending}
            onClick={() => {
              setRegister(!register)
              setError(null)
            }}
          >
            {register
              ? 'Already have an account? Sign in'
              : 'Create an organization'}
          </button>
        </div>
      </section>
    </main>
  )
}
