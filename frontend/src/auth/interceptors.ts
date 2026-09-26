import type { AxiosInstance, InternalAxiosRequestConfig } from 'axios'
import type { AuthSession } from './session.ts'

export function attachAuthentication(
  client: AxiosInstance,
  session: AuthSession,
) {
  client.interceptors.request.use((config) => {
    const tracked = config as InternalAxiosRequestConfig & {
      _sessionRevision?: number
    }
    if (
      tracked._sessionRevision !== undefined &&
      tracked._sessionRevision !== session.revision()
    ) {
      throw new Error('Session changed; request cancelled.')
    }
    tracked._sessionRevision = session.revision()
    const token = session.accessToken()
    if (token) config.headers.set('Authorization', `Bearer ${token}`)
    return config
  })
  client.interceptors.response.use(
    (response) => response,
    async (error) => {
      const config = error.config
      if (error.response?.status !== 401 || !config || config._authRetried) {
        if (error.response?.status === 401 && config?._authRetried)
          session.clear()
        return Promise.reject(error)
      }
      if (
        session.snapshot().status !== 'authenticated' ||
        config._sessionRevision !== session.revision()
      ) {
        return Promise.reject(error)
      }
      config._authRetried = true
      const current = session.accessToken()
      const sent = config.headers.get('Authorization')
      const token =
        current && sent !== `Bearer ${current}`
          ? current
          : await session.refresh()
      config.headers.set('Authorization', `Bearer ${token}`)
      return client.request(config)
    },
  )
}
