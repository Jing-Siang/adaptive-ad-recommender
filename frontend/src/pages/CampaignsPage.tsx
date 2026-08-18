import { useEffect, useState } from 'react'
import { ChevronDown, ChevronLeft, ChevronRight, ChevronsUpDown, ChevronUp, Info, Plus } from 'lucide-react'
import { listCampaigns } from '../api'
import type { CampaignListResponse } from '../types'
import { CAMPAIGN_CATEGORIES, categoryLabel } from '../categories'
import { StatusBadge } from '../components/StatusBadge'
import { CampaignFormModal } from '../components/CampaignFormModal'
import { Spinner } from '../components/Spinner'
import { Tooltip } from '../components/Tooltip'

const STATUS_OPTIONS = ['pending_review', 'needs_review', 'active', 'rejected', 'completed']
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]
const SEARCH_DEBOUNCE_MS = 350

type SortBy = 'created_at' | 'headline' | 'budget_total'

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

export function CampaignsPage() {
  const [data, setData] = useState<CampaignListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)

  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [reviewReasonSearchInput, setReviewReasonSearchInput] = useState('')
  const [reviewReasonSearch, setReviewReasonSearch] = useState('')
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState('')
  const [sortBy, setSortBy] = useState<SortBy>('created_at')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [refreshKey, setRefreshKey] = useState(0)

  // Debounce the search boxes so typing doesn't fire a request per
  // keystroke -- the actual query params only update once typing pauses.
  useEffect(() => {
    const id = setTimeout(() => setSearch(searchInput.trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [searchInput])

  useEffect(() => {
    const id = setTimeout(() => setReviewReasonSearch(reviewReasonSearchInput.trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [reviewReasonSearchInput])

  // Any filter/sort change invalidates the current page number -- e.g. page
  // 5 of an unfiltered list may not exist once a search/category/status
  // narrows the set, and a new sort order starts back at the top.
  useEffect(() => {
    setPage(1)
  }, [search, reviewReasonSearch, category, status, sortBy, sortDir, pageSize])

  useEffect(() => {
    let cancelled = false
    listCampaigns({
      status: status || undefined,
      category: category || undefined,
      search: search || undefined,
      reviewReasonSearch: reviewReasonSearch || undefined,
      sortBy,
      sortDir,
      page,
      pageSize,
    })
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [search, reviewReasonSearch, category, status, sortBy, sortDir, page, pageSize, refreshKey])

  function toggleSort(column: SortBy) {
    if (sortBy === column) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(column)
      setSortDir('asc')
    }
  }

  if (loading && !data) {
    return (
      <div className="fixed inset-y-0 left-56 right-0 flex items-center justify-center">
        <Spinner />
      </div>
    )
  }

  const campaigns = data?.items ?? []
  const totalPages = data?.total_pages ?? 1
  const total = data?.total ?? 0
  const hasFilters = Boolean(search || reviewReasonSearch || category || status)

  function sortIcon(column: SortBy) {
    if (sortBy !== column) return <ChevronsUpDown size={14} className="text-stone-300 dark:text-stone-600" />
    return sortDir === 'asc' ? <ChevronUp size={14} /> : <ChevronDown size={14} />
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <h1 className="mt-3 text-2xl font-semibold">Campaigns</h1>
          <Tooltip side="right" content="All submitted campaigns and their status.">
            <span className="mt-3.5 text-stone-500 dark:text-stone-400">
              <Info size={16} />
            </span>
          </Tooltip>
        </div>
        <button
          type="button"
          onClick={() => setShowModal(true)}
          className="mt-4 flex items-center gap-1.5 rounded-lg border border-stone-300 bg-stone-50 px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-100 dark:border-stone-600 dark:bg-stone-800 dark:text-stone-200 dark:hover:bg-stone-700"
        >
          <Plus size={16} />
          New campaign
        </button>
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

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
        <select
          value={pageSize}
          onChange={(e) => setPageSize(Number(e.target.value))}
          className={filterInputClass}
        >
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
            <col className="w-[28%]" />
            <col className="w-[16%]" />
            <col className="w-[14%]" />
            <col className="w-[18%]" />
            <col className="w-[24%]" />
          </colgroup>
          <thead>
            <tr className="border-b border-stone-200 text-stone-500 dark:border-stone-700 dark:text-stone-400">
              <th className="py-2 pr-4">
                <button
                  type="button"
                  onClick={() => toggleSort('headline')}
                  className="flex items-center gap-1 hover:text-stone-700 dark:hover:text-stone-200"
                >
                  Headline {sortIcon('headline')}
                </button>
              </th>
              <th className="py-2 pr-4">Category</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2 pr-4">
                <button
                  type="button"
                  onClick={() => toggleSort('budget_total')}
                  className="flex items-center gap-1 hover:text-stone-700 dark:hover:text-stone-200"
                >
                  Budget {sortIcon('budget_total')}
                </button>
              </th>
              <th className="py-2 pr-4">Review reason</th>
            </tr>
            {/* Inline per-column filters, directly under the headers they
                filter -- headline is a free-text search, category/status
                are exact-match dropdowns matching what the backend query
                actually supports. Budget/review reason aren't filterable. */}
            <tr className="border-b border-stone-200 dark:border-stone-700">
              <th className="py-1.5 pr-4 font-normal">
                <input
                  type="text"
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  placeholder="Search headline…"
                  className={`w-full ${filterInputClass}`}
                />
              </th>
              <th className="py-1.5 pr-4 font-normal">
                <select
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  className={`w-full ${filterInputClass}`}
                >
                  <option value="">All</option>
                  {CAMPAIGN_CATEGORIES.map((c) => (
                    <option key={c} value={c}>
                      {categoryLabel(c)}
                    </option>
                  ))}
                </select>
              </th>
              <th className="py-1.5 pr-4 font-normal">
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                  className={`w-full ${filterInputClass}`}
                >
                  <option value="">All</option>
                  {STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {categoryLabel(s)}
                    </option>
                  ))}
                </select>
              </th>
              <th className="py-1.5 pr-4" />
              <th className="py-1.5 pr-4 font-normal">
                <input
                  type="text"
                  value={reviewReasonSearchInput}
                  onChange={(e) => setReviewReasonSearchInput(e.target.value)}
                  placeholder="Search review reason…"
                  className={`w-full ${filterInputClass}`}
                />
              </th>
            </tr>
          </thead>
          <tbody>
            {campaigns.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-6 text-center text-stone-500 dark:text-stone-500">
                  {hasFilters ? 'No campaigns match these filters.' : 'No campaigns yet.'}
                </td>
              </tr>
            ) : (
              campaigns.map((c) => (
                <tr key={c.id} className="border-b border-stone-100 dark:border-stone-900">
                  <td className="truncate py-2 pr-4 font-medium" title={c.headline}>
                    {c.headline}
                  </td>
                  <td className="truncate py-2 pr-4 text-stone-600 dark:text-stone-400">
                    {categoryLabel(c.category)}
                  </td>
                  <td className="py-2 pr-4">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="truncate py-2 pr-4 text-stone-600 dark:text-stone-400">
                    ${c.budget_spent.toFixed(2)} / ${c.budget_total.toFixed(2)}
                  </td>
                  <td className="truncate py-2 pr-4 text-stone-500 dark:text-stone-500" title={c.review_reason ?? undefined}>
                    {c.review_reason ?? '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {showModal && (
        <CampaignFormModal
          onClose={() => setShowModal(false)}
          onCreated={() => {
            setShowModal(false)
            setRefreshKey((k) => k + 1)
          }}
        />
      )}
    </div>
  )
}
