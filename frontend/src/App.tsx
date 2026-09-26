import { WebhooksPage } from './pages/WebhooksPage'
import { JobsPage } from './pages/JobsPage'
import { AuthProvider, ProtectedRoutes } from './auth/AuthProvider'
import { UsersPage } from './pages/UsersPage'
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom'
import { AppLayout } from './components/layout/AppLayout'
import { DashboardPage } from './pages/DashboardPage'
import { IncidentsPage } from './pages/IncidentsPage'
import { UnitsPage } from './pages/UnitsPage'
import { LoginPage } from './pages/LoginPage'
import { OperationsProvider } from './features/operations/OperationsProvider'
import { useOperations } from './features/operations/context'

function IncidentRegister() {
  const { snapshot } = useOperations()
  return <IncidentsPage incidents={snapshot.incidents} />
}

function UnitRegister() {
  const { snapshot } = useOperations()
  return <UnitsPage units={snapshot.units} />
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<ProtectedRoutes />}>
            <Route
              element={
                <OperationsProvider>
                  <AppLayout />
                </OperationsProvider>
              }
            >
              <Route index element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/incidents" element={<IncidentRegister />} />
              <Route path="/units" element={<UnitRegister />} />
              <Route path="/users" element={<UsersPage />} />
              <Route path="/webhooks" element={<WebhooksPage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route
                path="*"
                element={
                  <div className="py-20 text-center">
                    <h1 className="page-title">Page not found</h1>
                    <Link
                      to="/dashboard"
                      className="mt-5 inline-block text-teal-700"
                    >
                      Return to operations overview →
                    </Link>
                  </div>
                }
              />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App
