import { useEffect, useState } from 'react'
import { ChevronDown, ChevronLeft, ChevronRight, ChevronsUpDown, ChevronUp, Info } from 'lucide-react'
import { fetchPerformance, fetchPerformanceCampaigns } from '../api'
import type { CampaignPerformanceListResponse, PerformanceResponse } from '../types'
import { categoryLabel } from '../categories'
import { StatTile } from '../components/StatTile'
import { CtrTrendChart } from '../components/CtrTrendChart'
import { StatusBadge } from '../components/StatusBadge'
import { Spinner } from '../components/Spinner'
import { Tooltip } from '../components/Tooltip'

function formatCompact(n: number): string {
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n)
}

function formatPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`
}

function formatUsd(n: number): string {
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', notation: 'compact' }).format(n)
}

type Tab = 'overview' | 'campaigns'

const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'campaigns', label: 'Campaigns' },
]

type SortBy = 'headline' | 'impressions' | 'likes' | 'dislikes' | 'conversions' | 'reports' | 'ctr' | 'spend'

const STATUS_OPTIONS = ['pending_review', 'needs_review', 'active', 'rejected', 'completed']
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]
const SEARCH_DEBOUNCE_MS = 350

// Matches CampaignFormModal's inputClass -- the app's one established
// input/select style -- just with tighter padding so it fits in a table's
// filter row instead of a form.
const filterInputClass = 'rounded border border-stone-300 px-2 py-1.5 text-sm dark:border-stone-700 dark:bg-stone-800'

/** Windowed page list with '…' gaps -- e.g. [1, '…', 4, 5, 6, '…', 20] --
 * so a catalog with hundreds of pages doesn't render hundreds of buttons. */
function getPageNumbers(current: number, total: number): (number | '…')[] {
  const keep = new Set([1, total, current - 1, current, current + 1])
  const pages = [...keep].filter((p) => p >= 1 && p <= total).sort((a, b) => a - b)
  const result: (number | '…')[] = []
  let prev = 0
  for (const p of pages) {
    if (prev && p - prev > 1) result.push('…')
    result.push(p)
    prev = p
  }
  return result
}

export function PerformancePage() {
  const [data, setData] = useState<PerformanceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('overview')

  const [campaignData, setCampaignData] = useState<CampaignPerformanceListResponse | null>(null)
  const [campaignError, setCampaignError] = useState<string | null>(null)
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [sortBy, setSortBy] = useState<SortBy>('impressions')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  useEffect(() => {
    fetchPerformance()
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  // Debounce the search box so typing doesn't fire a request per keystroke --
  // the actual query param only updates once typing pauses.
  useEffect(() => {
    const id = setTimeout(() => setSearch(searchInput.trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [searchInput])

  // Any filter/sort change invalidates the current page number -- e.g. page
  // 5 of an unfiltered list may not exist once a search/status narrows the
  // set, and a new sort order starts back at the top.
  useEffect(() => {
    setPage(1)
  }, [search, status, sortBy, sortDir, pageSize])

  useEffect(() => {
    if (tab !== 'campaigns') return
    let cancelled = false
    fetchPerformanceCampaigns({ status: status || undefined, search: search || undefined, sortBy, sortDir, page, pageSize })
      .then((result) => {
        if (!cancelled) setCampaignData(result)
      })
      .catch((err) => {
        if (!cancelled) setCampaignError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [tab, search, status, sortBy, sortDir, page, pageSize])

  function toggleSort(column: SortBy) {
    if (sortBy === column) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(column)
      setSortDir('asc')
    }
  }

  function sortIcon(column: SortBy) {
    if (sortBy !== column) return <ChevronsUpDown size={14} className="text-stone-300 dark:text-stone-600" />
    return sortDir === 'asc' ? <ChevronUp size={14} /> : <ChevronDown size={14} />
  }

  if (error) {
    return (
      <div className="mx-auto max-w-6xl p-6">
        <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="fixed inset-y-0 left-56 right-0 flex items-center justify-center">
        <Spinner />
      </div>
    )
  }

  const { totals, trend } = data
  const campaigns = campaignData?.items ?? []
  const totalPages = campaignData?.total_pages ?? 1
  const total = campaignData?.total ?? 0
  const hasFilters = Boolean(search || status)

  function sortableHeader(column: SortBy, label: string) {
    return (
      <button
        type="button"
        onClick={() => toggleSort(column)}
        className="flex items-center gap-1 hover:text-stone-700 dark:hover:text-stone-200"
      >
        {label} {sortIcon(column)}
      </button>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8 p-6">
      <div className="flex items-center gap-1.5">
        <h1 className="mt-3 text-2xl font-semibold">Performance</h1>
        <Tooltip side="right" content="Totals across all users and campaigns, not just your own activity.">
          <span className="mt-3.5 text-stone-500 dark:text-stone-400">
            <Info size={16} />
          </span>
        </Tooltip>
      </div>

      <div className="flex gap-1 border-b border-stone-200 dark:border-stone-700">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${
              tab === id
                ? 'border-stone-900 text-stone-900 dark:border-stone-100 dark:text-stone-100'
                : 'border-transparent text-stone-500 hover:text-stone-700 dark:text-stone-400 dark:hover:text-stone-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'overview' ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <StatTile label="Impressions" value={formatCompact(totals.impressions)} />
            <StatTile label="CTR" value={formatPct(totals.ctr)} />
            <StatTile label="Engagement rate" value={formatPct(totals.engagement_rate)} />
            <StatTile label="Dislike rate" value={formatPct(totals.dislike_rate)} />
            <StatTile label="Total spend" value={formatUsd(totals.total_spend)} />
            <StatTile label="Avg. CPA" value={totals.avg_cpa === null ? '—' : formatUsd(totals.avg_cpa)} />
          </div>

          <div className="rounded border border-stone-200 p-4 dark:border-stone-700">
            <CtrTrendChart data={trend} />
          </div>
        </>
      ) : (
        <div className="space-y-4">
          {campaignError && <p className="text-sm text-red-600 dark:text-red-400">{campaignError}</p>}

          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-stone-600 dark:text-stone-400">
            <span>Total: {total}</span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                aria-label="Previous page"
                className="grid place-items-center rounded-lg border border-stone-300 bg-stone-50 p-1.5 text-stone-700 hover:bg-stone-100 disabled:opacity-40 disabled:hover:bg-stone-50 dark:border-stone-600 dark:bg-stone-800 dark:text-stone-200 dark:hover:bg-stone-700 dark:disabled:hover:bg-stone-800"
              >
                <ChevronLeft size={16} />
              </button>
              {getPageNumbers(page, totalPages).map((p, i) =>
                p === '…' ? (
                  <span key={`ellipsis-${i}`} className="px-1.5 text-stone-400 dark:text-stone-600">
                    …
                  </span>
                ) : (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setPage(p)}
                    aria-current={p === page ? 'page' : undefined}
                    className={`min-w-8 rounded-lg px-2 py-1.5 text-sm font-medium transition ${
                      p === page
                        ? 'bg-stone-100 text-stone-900 dark:bg-stone-700 dark:text-stone-100'
                        : 'text-stone-600 hover:bg-stone-100/60 hover:text-stone-900 dark:text-stone-400 dark:hover:bg-stone-700/50 dark:hover:text-stone-100'
                    }`}
                  >
                    {p}
                  </button>
                ),
              )}
              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                aria-label="Next page"
                className="grid place-items-center rounded-lg border border-stone-300 bg-stone-50 p-1.5 text-stone-700 hover:bg-stone-100 disabled:opacity-40 disabled:hover:bg-stone-50 dark:border-stone-600 dark:bg-stone-800 dark:text-stone-200 dark:hover:bg-stone-700 dark:disabled:hover:bg-stone-800"
              >
                <ChevronRight size={16} />
              </button>
            </div>
            <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} className={filterInputClass}>
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n} / page
                </option>
              ))}
            </select>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full table-fixed text-left text-sm">
              <colgroup>
                <col className="w-[30%]" />
                <col className="w-[10%]" />
                <col className="w-[10%]" />
                <col className="w-[8%]" />
                <col className="w-[8%]" />
                <col className="w-[8%]" />
                <col className="w-[8%]" />
                <col className="w-[8%]" />
                <col className="w-[10%]" />
              </colgroup>
              <thead>
                <tr className="border-b border-stone-200 text-stone-500 dark:border-stone-700 dark:text-stone-400">
                  <th className="py-2 pr-4">{sortableHeader('headline', 'Campaign')}</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">{sortableHeader('impressions', 'Impr.')}</th>
                  <th className="py-2 pr-4">{sortableHeader('likes', 'Likes')}</th>
                  <th className="py-2 pr-4">{sortableHeader('dislikes', 'Dislikes')}</th>
                  <th className="py-2 pr-4">{sortableHeader('conversions', 'Conv.')}</th>
                  <th className="py-2 pr-4">{sortableHeader('reports', 'Reports')}</th>
                  <th className="py-2 pr-4">{sortableHeader('ctr', 'CTR')}</th>
                  <th className="py-2 pr-4">{sortableHeader('spend', 'Spend')}</th>
                </tr>
                {/* Inline per-column filters, matching the Campaigns page --
                    campaign name is a free-text search, status is an
                    exact-match dropdown. The numeric columns aren't
                    filterable, only sortable (via their header above). */}
                <tr className="border-b border-stone-200 dark:border-stone-700">
                  <th className="py-1.5 pr-4 font-normal">
                    <input
                      type="text"
                      value={searchInput}
                      onChange={(e) => setSearchInput(e.target.value)}
                      placeholder="Search campaign…"
                      className={`w-full ${filterInputClass}`}
                    />
                  </th>
                  <th className="py-1.5 pr-4 font-normal">
                    <select value={status} onChange={(e) => setStatus(e.target.value)} className={`w-full ${filterInputClass}`}>
                      <option value="">All</option>
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {categoryLabel(s)}
                        </option>
                      ))}
                    </select>
                  </th>
                  <th className="py-1.5 pr-4" colSpan={7} />
                </tr>
              </thead>
              <tbody className="[font-variant-numeric:tabular-nums]">
                {campaignData === null ? (
                  <tr>
                    <td colSpan={9} className="py-6 text-center text-stone-500 dark:text-stone-500">
                      <Spinner label="" />
                    </td>
                  </tr>
                ) : campaigns.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="py-6 text-center text-stone-500 dark:text-stone-500">
                      {hasFilters ? 'No campaigns match these filters.' : 'No campaigns yet.'}
                    </td>
                  </tr>
                ) : (
                  campaigns.map((c) => (
                    <tr key={c.campaign_id} className="border-b border-stone-100 dark:border-stone-900">
                      <td className="truncate py-2 pr-4 font-medium" title={c.headline}>
                        {c.headline}
                      </td>
                      <td className="py-2 pr-4">
                        <StatusBadge status={c.status} />
                      </td>
                      <td className="py-2 pr-4">{c.impressions}</td>
                      <td className="py-2 pr-4">{c.likes}</td>
                      <td className="py-2 pr-4">{c.dislikes}</td>
                      <td className="py-2 pr-4">{c.conversions}</td>
                      <td className="py-2 pr-4">{c.reports > 0 ? c.reports : '—'}</td>
                      <td className="py-2 pr-4">{formatPct(c.ctr)}</td>
                      <td className="py-2 pr-4">{formatUsd(c.spend)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
