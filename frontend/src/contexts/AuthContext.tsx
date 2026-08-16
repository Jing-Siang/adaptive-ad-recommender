import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { fetchMe, googleLogin, loadStoredAccessToken, logout as logoutRequest } from '../api'
import type { Account } from '../types'

interface AuthContextValue {
  user: Account | null
  loading: boolean
  login: (idToken: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Account | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Priming from localStorage, then asking the server who that token
    // belongs to, covers all three startup cases in one path: a still-valid
    // cached token (fetchMe succeeds immediately), an expired one (fetchMe's
    // own 401-retry inside request() silently refreshes via the httpOnly
    // cookie and retries), or no session at all (both fail, user stays null).
    loadStoredAccessToken()
    fetchMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  async function login(idToken: string) {
    const { user: loggedInUser } = await googleLogin(idToken)
    setUser(loggedInUser)
  }

  async function logout() {
    await logoutRequest()
    setUser(null)
  }

  return <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
