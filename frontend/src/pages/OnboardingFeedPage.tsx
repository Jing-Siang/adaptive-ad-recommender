import { useState } from 'react'
import { OnboardingChat } from '../components/OnboardingChat'
import { Feed } from '../components/Feed'

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
        <Feed userId={userId} />
      )}
    </div>
  )
}
