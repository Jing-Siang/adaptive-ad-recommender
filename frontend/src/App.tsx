import { NavLink, Route, Routes } from 'react-router-dom'
import { OnboardingFeedPage } from './pages/OnboardingFeedPage'
import { PerformancePage } from './pages/PerformancePage'
import { CampaignsPage } from './pages/CampaignsPage'
import { ModeratorPage } from './pages/ModeratorPage'

const NAV_LINKS = [
  { to: '/', label: 'Feed', end: true },
  { to: '/performance', label: 'Performance' },
  { to: '/campaigns', label: 'Campaigns' },
  { to: '/moderator', label: 'Moderator' },
]

function App() {
  return (
    <div className="min-h-svh bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="border-b border-slate-200 dark:border-slate-800">
        <nav className="mx-auto flex max-w-5xl items-center gap-6 px-6 py-3">
          <span className="font-semibold">Adaptive Ad Recommender</span>
          <div className="flex gap-4 text-sm">
            {NAV_LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                className={({ isActive }) =>
                  isActive
                    ? 'font-medium text-indigo-600 dark:text-indigo-400'
                    : 'text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100'
                }
              >
                {link.label}
              </NavLink>
            ))}
          </div>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<OnboardingFeedPage />} />
          <Route path="/performance" element={<PerformancePage />} />
          <Route path="/campaigns" element={<CampaignsPage />} />
          <Route path="/moderator" element={<ModeratorPage />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
