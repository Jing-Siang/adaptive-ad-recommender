import { GoogleLogin, type CredentialResponse } from '@react-oauth/google'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  async function handleSuccess(credentialResponse: CredentialResponse) {
    if (!credentialResponse.credential) {
      setError('Google did not return a credential -- try again.')
      return
    }
    try {
      await login(credentialResponse.credential)
      // Always land on the feed after signing in, regardless of whatever
      // URL happened to be active before login (e.g. a bookmarked
      // /moderator or /campaigns route, or a page reload mid-session that
      // triggered re-auth) -- LoginPage renders outside <Routes>, so the
      // current URL doesn't otherwise change on its own when `user` flips
      // from null to set.
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="flex h-svh items-center justify-center bg-stone-50 dark:bg-stone-900">
      <div className="flex w-full max-w-sm flex-col items-center gap-6 rounded-xl border border-stone-200 bg-white p-8 text-center dark:border-stone-700 dark:bg-stone-800">
        <div>
          <h1 className="text-lg font-semibold text-stone-900 dark:text-stone-100">Adaptive Ad Recommender</h1>
          <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">Sign in to continue</p>
        </div>
        <GoogleLogin onSuccess={handleSuccess} onError={() => setError('Google sign-in failed -- try again.')} />
        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      </div>
    </div>
  )
}
