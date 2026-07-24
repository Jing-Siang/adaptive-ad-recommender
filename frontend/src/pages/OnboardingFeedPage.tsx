import { useState } from 'react'
import { OnboardingChat } from '../components/OnboardingChat'

function newUserId(): string {
  return crypto.randomUUID()
}

export function OnboardingFeedPage() {
  const [userId, setUserId] = useState(newUserId)
  const [mode, setMode] = useState<'onboarding' | 'feed'>('onboarding')

  function handleReset() {
    setUserId(newUserId())
    setMode('onboarding')
  }

  return (
    <div>
      <div className="mx-auto flex max-w-2xl justify-end px-6 pt-3">
        <button
          type="button"
          onClick={handleReset}
          className="text-xs text-slate-500 hover:underline dark:text-slate-500"
        >
          Restart onboarding
        </button>
      </div>
      {mode === 'onboarding' ? (
        <OnboardingChat userId={userId} onFinish={() => setMode('feed')} />
      ) : (
        <div className="mx-auto max-w-2xl p-6">
          <h1 className="text-2xl font-semibold">Feed</h1>
          <p className="mt-2 text-slate-600 dark:text-slate-400">Coming soon.</p>
        </div>
      )}
    </div>
  )
}
