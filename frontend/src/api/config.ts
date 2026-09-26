// Use the same hostname as the UI so SameSite cookies work on localhost and 127.0.0.1.
export const API_URL = (
  import.meta.env.VITE_API_URL ??
  (import.meta.env.PROD ? window.location.origin : `${window.location.protocol}//${window.location.hostname}:8000`)
).replace(/\/$/, '')
