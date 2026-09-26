import type { ActivityEvent } from '../../types/domain'
import { Icon } from '../common/Icon'

const activityTime = new Intl.DateTimeFormat('en-SG', {
  timeZone: 'Asia/Singapore',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const eventAppearance = {
  selected: { icon: 'pin', className: 'bg-slate-100 text-slate-500' },
  dispatched: { icon: 'truck', className: 'bg-indigo-50 text-indigo-600' },
  en_route: { icon: 'arrow', className: 'bg-blue-50 text-blue-600' },
  arrived: { icon: 'check', className: 'bg-teal-50 text-teal-700' },
  reset: { icon: 'reset', className: 'bg-amber-50 text-amber-700' },
} as const

export function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  return (
    <section
      aria-label="Activity feed"
      className="overflow-hidden rounded-xl border border-slate-200 bg-white"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-5 py-4">
        <h2 className="text-sm font-semibold text-slate-800">
          Activity timeline
        </h2>
        <p className="text-[10px] text-slate-500">Latest first · SGT</p>
      </div>
      <p role="status" className="sr-only">
        {events[0] ? `Latest activity: ${events[0].message}` : ''}
      </p>
      {events.length ? (
        <div
          tabIndex={0}
          aria-label="Recent simulation activity"
          className="max-h-80 overflow-y-auto"
        >
          <ol className="divide-y divide-slate-100 px-5">
            {events.map((event) => {
              const kind =
                event.eventType === 'demo.reset'
                  ? 'reset'
                  : event.eventType === 'dispatch.created'
                    ? 'dispatched'
                    : event.eventType === 'dispatch.updated' || event.eventType === 'incident.resolved'
                      ? 'arrived'
                      : event.eventType === 'unit.status.updated'
                        ? 'en_route'
                        : 'selected'
              const appearance = eventAppearance[kind]
              return (
                <li key={event.id} className="flex items-start gap-3 py-4">
                  <span
                    className={`mt-0.5 rounded-full p-1.5 ${appearance.className}`}
                  >
                    <Icon name={appearance.icon} className="h-3 w-3" />
                  </span>
                  <div className="min-w-0">
                    <p className="text-xs leading-5 text-slate-700">
                      {event.message}
                    </p>
                    <time
                      dateTime={event.timestamp}
                      className="mt-1 block font-mono text-[10px] text-slate-400"
                    >
                      {activityTime.format(new Date(event.timestamp))} SGT
                    </time>
                  </div>
                </li>
              )
            })}
          </ol>
        </div>
      ) : (
        <div className="px-5 py-8 text-center">
          <Icon name="clock" className="mx-auto h-6 w-6 text-slate-400" />
          <p className="mt-3 text-sm font-medium text-slate-700">
            Ready for your first dispatch
          </p>
          <p className="mt-2 text-xs leading-5 text-slate-500">
            Select an incident to begin. Dispatch and arrival events will appear
            here.
          </p>
        </div>
      )}
    </section>
  )
}
