import { useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { completeOnboarding, fetchMe, resetProfile } from '../api'
import { useAuth } from '../contexts/AuthContext'
import { OnboardingChat } from '../components/OnboardingChat'
import { Feed } from '../components/Feed'
import { Spinner } from '../components/Spinner'

export function OnboardingFeedPage() {
  const { user, updateUser } = useAuth()
  // user.onboarding_completed, not "does a profile vector exist" --
  // a profile vector gets seeded much earlier, on the first onboarding
  // checkpoint round that shows candidates, so using that instead would
  // route a mid-conversation reload straight to the feed (see
  // docs/auth_plan.md and app/serving/onboarding_api.py's /complete route).
  const [mode, setMode] = useState<'onboarding' | 'feed'>(user?.onboarding_completed ? 'feed' : 'onboarding')
  const [chatKey, setChatKey] = useState(0)
  const [resetting, setResetting] = useState(false)

  // App.tsx never renders this page without a logged-in user.
  if (!user) return null

  async function handleFinish() {
    const updated = await completeOnboarding()
    updateUser(updated)
    setMode('feed')
  }

  async function handleReset() {
    setResetting(true)
    try {
      await resetProfile()
      const updated = await fetchMe()
      updateUser(updated)
      setChatKey((k) => k + 1)
      setMode('onboarding')
    } finally {
      setResetting(false)
    }
  }

  return (
    <div className="flex min-h-full flex-col">
      <div className="sticky top-0 z-10">
        <div className="flex justify-end bg-stone-50 px-6 pt-4 dark:bg-stone-900">
          <button
            type="button"
            onClick={handleReset}
            disabled={resetting}
            className="grid place-items-center rounded-lg border border-stone-300 bg-stone-50 px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-100 disabled:opacity-60 disabled:hover:bg-stone-50 dark:border-stone-600 dark:bg-stone-800 dark:text-stone-200 dark:hover:bg-stone-700 dark:disabled:hover:bg-stone-800"
          >
            {/* Both states occupy the same grid cell -- the button's width
                is always driven by the wider one (icon + text), so
                swapping to the smaller spinner never shrinks it.
                visibility (not conditional rendering) keeps the hidden
                one contributing to that sizing. */}
            <span className={`col-start-1 row-start-1 flex items-center gap-1.5 ${resetting ? 'invisible' : ''}`}>
              <RotateCcw size={16} />
              Restart onboarding
            </span>
            <span className={`col-start-1 row-start-1 ${resetting ? '' : 'invisible'}`}>
              <Spinner label="" />
            </span>
          </button>
        </div>
        {/* Fades scrolling content out before it disappears under the solid
            bar above, instead of a hard cutoff. */}
        <div className="pointer-events-none h-6 bg-gradient-to-b from-stone-50 to-transparent dark:from-stone-900" />
      </div>
      <div className="flex flex-1 flex-col">
        {mode === 'onboarding' ? <OnboardingChat key={chatKey} onFinish={handleFinish} /> : <Feed />}
      </div>
    </div>
  )
}
