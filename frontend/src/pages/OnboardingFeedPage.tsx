import { useEffect, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { getUser, resetProfile } from '../api'
import { OnboardingChat } from '../components/OnboardingChat'
import { Feed } from '../components/Feed'
import { Spinner } from '../components/Spinner'

export function OnboardingFeedPage() {
  const [mode, setMode] = useState<'onboarding' | 'feed' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [chatKey, setChatKey] = useState(0)

  useEffect(() => {
    // GET /users/me 404s until the first onboarding checkpoint seeds a
    // profile vector -- that's the whole signal for "has this account been
    // through onboarding before," no separate progress tracking needed
    // (see docs/auth_plan.md). Any other failure (network, 500) is a real
    // error, not "no profile yet" -- surfaced instead of silently sending
    // an existing account back through onboarding.
    getUser()
      .then(() => setMode('feed'))
      .catch((err) => {
        if (err instanceof Error && err.message.includes('404')) {
          setMode('onboarding')
        } else {
          setError(err instanceof Error ? err.message : String(err))
        }
      })
  }, [])

  async function handleReset() {
    await resetProfile()
    setChatKey((k) => k + 1)
    setMode('onboarding')
  }

  return (
    <div className="flex min-h-full flex-col">
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
        {error ? (
          <p className="p-6 text-sm text-red-600 dark:text-red-400">{error}</p>
        ) : mode === null ? (
          <div className="flex flex-1 items-center justify-center">
            <Spinner label="Loading" />
          </div>
        ) : mode === 'onboarding' ? (
          <OnboardingChat key={chatKey} onFinish={() => setMode('feed')} />
        ) : (
          <Feed />
        )}
      </div>
    </div>
  )
}
