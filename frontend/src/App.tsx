import { NavLink, Route, Routes } from 'react-router-dom'
import { MessageSquare, BarChart3, Megaphone, ShieldCheck } from 'lucide-react'
import SimpleBar from 'simplebar-react'
import 'simplebar-react/dist/simplebar.min.css'
import { OnboardingFeedPage } from './pages/OnboardingFeedPage'
import { PerformancePage } from './pages/PerformancePage'
import { CampaignsPage } from './pages/CampaignsPage'
import { ModeratorPage } from './pages/ModeratorPage'

const NAV_LINKS = [
  { to: '/', label: 'Feed', end: true, Icon: MessageSquare },
  { to: '/performance', label: 'Performance', Icon: BarChart3 },
  { to: '/campaigns', label: 'Campaigns', Icon: Megaphone },
  { to: '/moderator', label: 'Moderator', Icon: ShieldCheck },
]

function App() {
  return (
    <div className="flex h-svh bg-stone-50 text-stone-900 dark:bg-stone-900 dark:text-stone-100">
      <aside className="flex w-56 shrink-0 flex-col gap-1 border-r border-stone-200 bg-white p-3 dark:border-stone-700 dark:bg-stone-800">
        <span className="px-2.5 py-2 text-sm font-semibold">Adaptive Ad Recommender</span>
        <nav className="mt-1 flex flex-col gap-0.5">
          {NAV_LINKS.map(({ to, label, end, Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition ${
                  isActive
                    ? 'bg-stone-100 text-stone-900 dark:bg-stone-700 dark:text-stone-100'
                    : 'text-stone-600 hover:bg-stone-100/60 hover:text-stone-900 dark:text-stone-400 dark:hover:bg-stone-700/50 dark:hover:text-stone-100'
                }`
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="min-h-0 flex-1">
        <SimpleBar style={{ height: '100%' }}>
          <Routes>
            <Route path="/" element={<OnboardingFeedPage />} />
            <Route path="/performance" element={<PerformancePage />} />
            <Route path="/campaigns" element={<CampaignsPage />} />
            <Route path="/moderator" element={<ModeratorPage />} />
          </Routes>
        </SimpleBar>
      </main>
    </div>
  )
}

export default App
