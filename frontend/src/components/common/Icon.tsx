import type { CSSProperties } from 'react'

const paths = {
  grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  alert: 'm12 3 10 18H2L12 3z M12 9v5 M12 17v.1',
  truck: 'M3 6h11v12H3z M14 10h4l3 4v4h-7 M7 18v.1 M17 18v.1 M5 21h4 M15 21h4',
  clock: 'M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  pin: 'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0z M15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  check: 'm5 12 4 4L19 6',
  reset: 'M3 10a9 9 0 1 1 2 9 M3 3v7h7',
} as const

export function Icon({
  name,
  className = '',
  style,
}: {
  name: keyof typeof paths
  className?: string
  style?: CSSProperties
}) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`h-5 w-5 shrink-0 ${className}`}
      style={style}
    >
      <path d={paths[name]} />
    </svg>
  )
}
