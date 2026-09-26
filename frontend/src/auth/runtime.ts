import axios from 'axios'
import { API_URL } from '../api/config'
import { AuthSession } from './session'
const transport = axios.create({
  baseURL: `${API_URL}/api/auth`,
  withCredentials: true,
  timeout: 30_000,
})
export const authSession = new AuthSession({
  login: async (body) => (await transport.post('/login', body)).data,
  register: async (body) => (await transport.post('/register', body)).data,
  refresh: async () => (await transport.post('/refresh')).data,
  logout: async () => {
    await transport.post('/logout')
  },
  me: async (token) =>
    (
      await transport.get('/me', {
        headers: { Authorization: `Bearer ${token}` },
      })
    ).data,
})
