import { attachAuthentication } from '../auth/interceptors'
import axios from 'axios'

import { API_URL } from './config'
import { authSession } from '../auth/runtime'
export { API_URL } from './config'

export const client = axios.create({
  baseURL: `${API_URL}/api`,
  timeout: 10000,
})

attachAuthentication(client, authSession)

export function apiErrorMessage(error: unknown): string {
  if (axios.isAxiosError<{ error?: { message?: string } }>(error)) {
    return (
      error.response?.data?.error?.message ??
      (error.response
        ? `Request failed (HTTP ${error.response.status}). Please retry.`
        : 'Cannot reach the API. Check that FastAPI is running and retry.')
    )
  }
  return error instanceof Error
    ? error.message
    : 'An unexpected error occurred. Please retry.'
}
