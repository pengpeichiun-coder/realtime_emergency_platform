import { useEffect, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import { Navigate, Outlet } from 'react-router-dom'
import { authSession } from './runtime'
import { AuthContext, useAuth } from './context'
export function AuthProvider({ children }: { children: ReactNode }) {
  const state = useSyncExternalStore(
    authSession.subscribe,
    authSession.snapshot,
  )
  useEffect(() => {
    void authSession.restore()
  }, [])
  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>
}
export function ProtectedRoutes() {
  const { status } = useAuth()
  if (status === 'loading')
    return (
      <p role="status" className="p-8">
        Restoring your session…
      </p>
    )
  return status === 'authenticated' ? (
    <Outlet />
  ) : (
    <Navigate to="/login" replace />
  )
}
