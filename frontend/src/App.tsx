import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { MessageSquare, BarChart3, Megaphone, ShieldCheck, LogOut } from 'lucide-react'
import SimpleBar from 'simplebar-react'
import 'simplebar-react/dist/simplebar.min.css'
import { OnboardingFeedPage } from './pages/OnboardingFeedPage'
import { PerformancePage } from './pages/PerformancePage'
import { CampaignsPage } from './pages/CampaignsPage'
import { ModeratorPage } from './pages/ModeratorPage'
import { LoginPage } from './pages/LoginPage'
import { Spinner } from './components/Spinner'
import { useAuth } from './contexts/AuthContext'
import type { Role } from './types'

const ROLE_LABELS: Record<Role, string> = {
  end_user: 'User',
  advertiser: 'Advertiser',
  moderator: 'Moderator',
}

const NAV_LINKS: { to: string; label: string; end?: boolean; Icon: typeof MessageSquare; roles?: Role[] }[] = [
  { to: '/', label: 'Feed', end: true, Icon: MessageSquare },
  { to: '/performance', label: 'Performance', Icon: BarChart3, roles: ['advertiser', 'moderator'] },
  { to: '/campaigns', label: 'Campaigns', Icon: Megaphone, roles: ['advertiser', 'moderator'] },
  { to: '/moderator', label: 'Moderator', Icon: ShieldCheck, roles: ['moderator'] },
]

/** Defense beyond just hiding the nav link -- someone can still type the
 * URL directly, so the route itself checks the role too. Redirects to the
 * feed rather than showing a "no access" message, same as an unmatched
 * URL (see the catch-all route below) -- neither case is worth a dead-end
 * page, just send them back to the one page everyone can always see. */
function RequireRole({ roles, children }: { roles: Role[]; children: React.ReactNode }) {
  const { user } = useAuth()
  if (!user || !roles.includes(user.role)) {
    return <Navigate to="/" replace />
  }
  return <>{children}</>
}

function App() {
  const { user, loading, logout } = useAuth()
  const location = useLocation()
  const onFeedRoute = location.pathname === '/'

  if (loading) {
    return (
      <div className="flex h-svh items-center justify-center bg-stone-50 dark:bg-stone-900">
        <Spinner label="Loading" />
      </div>
    )
  }

  if (!user) {
    return <LoginPage />
  }

  const visibleLinks = NAV_LINKS.filter((link) => !link.roles || link.roles.includes(user.role))

  return (
    <div className="flex h-svh bg-stone-50 text-stone-900 dark:bg-stone-900 dark:text-stone-100">
      <aside className="flex w-56 shrink-0 flex-col gap-1 border-r border-stone-200 bg-white p-3 dark:border-stone-700 dark:bg-stone-800">
        <span className="px-2.5 py-2 text-sm font-semibold">Adaptive Ad Recommender</span>
        <nav className="mt-1 flex flex-col gap-0.5">
          {visibleLinks.map(({ to, label, end, Icon }) => (
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
        <div className="mt-auto flex items-center gap-2 border-t border-stone-200 pt-3 dark:border-stone-700">
          {user.avatar_url && <img src={user.avatar_url} alt="" className="h-7 w-7 rounded-full" />}
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium">{user.display_name}</p>
            <p className="truncate text-[11px] text-stone-500 dark:text-stone-400">{ROLE_LABELS[user.role]}</p>
          </div>
          <button
            type="button"
            title="Log out"
            onClick={() => logout()}
            className="rounded-lg p-1.5 text-stone-400 hover:bg-stone-100 hover:text-stone-700 dark:hover:bg-stone-700 dark:hover:text-stone-200"
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <main className="min-h-0 flex-1">
        <SimpleBar style={{ height: '100%' }}>
          {/* Always mounted, never torn down by route changes -- otherwise
              navigating to another page and back would unmount
              OnboardingChat and lose the in-progress conversation (its
              state is plain component state, not persisted anywhere on
              purpose, see docs/auth_plan.md). Hidden via CSS instead of
              being routed, so it keeps its state across every other page
              visit; only a real page reload loses it. */}
          <div className={onFeedRoute ? undefined : 'hidden'}>
            <OnboardingFeedPage />
          </div>
          {!onFeedRoute && (
            <Routes>
              <Route
                path="/performance"
                element={
                  <RequireRole roles={['advertiser', 'moderator']}>
                    <PerformancePage />
                  </RequireRole>
                }
              />
              <Route
                path="/campaigns"
                element={
                  <RequireRole roles={['advertiser', 'moderator']}>
                    <CampaignsPage />
                  </RequireRole>
                }
              />
              <Route
                path="/moderator"
                element={
                  <RequireRole roles={['moderator']}>
                    <ModeratorPage />
                  </RequireRole>
                }
              />
              {/* Any unmatched URL -- typos, stale links, made-up paths --
                  lands back on the feed instead of a blank page. */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          )}
        </SimpleBar>
      </main>
    </div>
  )
}

export default App
