import { createContext, useContext } from 'react'
import type { AuthState } from './session'
export const AuthContext = createContext<AuthState>({
  status: 'loading',
  user: null,
})
export const useAuth = () => useContext(AuthContext)
