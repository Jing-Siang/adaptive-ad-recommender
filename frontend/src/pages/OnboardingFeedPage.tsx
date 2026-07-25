import { useState } from 'react'
import { RotateCcw } from 'lucide-react'
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
    <div className="flex h-full flex-col">
      <div className="sticky top-0 z-10">
        <div className="flex justify-end bg-stone-50 px-6 pt-4 dark:bg-stone-900">
          <button
            type="button"
            onClick={handleReset}
            className="flex items-center gap-1.5 rounded-lg border border-stone-300 bg-stone-50 px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-100 dark:border-stone-600 dark:bg-stone-800 dark:text-stone-200 dark:hover:bg-stone-700"
          >
            <RotateCcw size={16} />
            Restart onboarding
          </button>
        </div>
        {/* Fades scrolling content out before it disappears under the solid
            bar above, instead of a hard cutoff. */}
        <div className="pointer-events-none h-6 bg-gradient-to-b from-stone-50 to-transparent dark:from-stone-900" />
      </div>
      <div className="flex flex-1 flex-col">
        {mode === 'onboarding' ? (
          <OnboardingChat userId={userId} onFinish={() => setMode('feed')} />
        ) : (
          <Feed userId={userId} />
        )}
      </div>
    </div>
  )
}
