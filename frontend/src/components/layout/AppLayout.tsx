import { useAuth } from '../../auth/context'
import { authSession } from '../../auth/runtime'
import { canAdmin } from '../../auth/session'
import { NavLink, Outlet, Link } from 'react-router-dom'
import { Icon } from '../common/Icon'
import { OperationsGate } from '../../features/operations/OperationsGate'

const navigation = [
  { to: '/dashboard', label: 'Overview', icon: 'grid' },
  { to: '/incidents', label: 'Incidents', icon: 'alert' },
  { to: '/units', label: 'Response units', icon: 'truck' },
] as const

export function AppLayout() {
  const { user } = useAuth()
  return (
    <div className="min-h-screen bg-[#f5f6f8] lg:flex">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      <aside className="bg-[#132a34] text-white lg:fixed lg:inset-y-0 lg:w-56">
        <Link
          to="/dashboard"
          className="flex items-center gap-3 px-6 py-7"
          aria-label="Command, dashboard"
        >
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#ef684b] text-3xl font-light">
            +
          </span>
          <span>
            <span className="block text-base font-bold tracking-[0.14em]">
              COMMAND
            </span>
            <span className="whitespace-nowrap text-[8px] tracking-[0.12em] text-slate-400">
              EMERGENCY RESPONSE
            </span>
          </span>
        </Link>
        <div className="hidden px-6 pb-4 pt-7 text-[10px] font-semibold tracking-[0.2em] text-slate-400 lg:block">
          WORKSPACE
        </div>
        <nav
          aria-label="Main navigation"
          className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-col lg:gap-2"
        >
          {navigation.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 whitespace-nowrap rounded-lg px-4 py-3 text-sm font-medium transition-colors ${isActive ? 'bg-[#29414b] text-white' : 'text-slate-300 hover:bg-white/5 hover:text-white'}`
              }
            >
              <Icon name={icon} />
              {label}
            </NavLink>
          ))}
          <NavLink to="/jobs" className="px-4 py-3 text-sm">
            Jobs and reports
          </NavLink>
          {canAdmin(user) && (
            <NavLink to="/webhooks" className="px-4 py-3 text-sm">
              Webhooks
            </NavLink>
          )}
          {canAdmin(user) && (
            <NavLink to="/users" className="px-4 py-3 text-sm">
              Manage users
            </NavLink>
          )}
        </nav>
        <div className="absolute bottom-6 mx-5 hidden rounded-xl border border-white/10 bg-white/5 p-4 lg:block">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold">
            <span className="h-1.5 w-1.5 rounded-full bg-teal-300" />
            Singapore operations
          </div>
          <p className="text-xs leading-5 text-slate-400">
            A demo command center.
            <br />
            All incidents and units are fictional.
          </p>
        </div>
      </aside>
      <div className="min-w-0 flex-1 lg:ml-56">
        <header className="flex min-h-20 flex-wrap items-center justify-between gap-3 border-b border-slate-200/80 bg-white px-5 py-4 sm:px-8">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Icon name="pin" className="h-4 w-4" />
            <span>Singapore</span>
            <span className="px-2 text-slate-300">/</span>
            <span className="font-medium text-slate-700">Command center</span>
          </div>
          <div className="flex items-center gap-5">
            <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-[11px] font-semibold text-amber-800">
              DEMO WORKSPACE
            </span>
            <span className="text-sm text-slate-700">
              {user?.display_name} · {user?.role}
            </span>
            <button
              className="text-sm text-teal-700"
              onClick={() => void authSession.logout()}
            >
              Sign out
            </button>
          </div>
        </header>
        <main id="main-content" className="mx-auto max-w-[1680px] p-5 sm:p-8">
          <OperationsGate>
            <Outlet />
          </OperationsGate>
        </main>
        <footer className="flex flex-wrap justify-between gap-2 px-5 pb-5 text-[11px] text-slate-400 sm:px-8">
          <span>COMMAND / Emergency Response Platform</span>
          <span>Fictional data · Singapore time (SGT)</span>
        </footer>
      </div>
    </div>
  )
}
