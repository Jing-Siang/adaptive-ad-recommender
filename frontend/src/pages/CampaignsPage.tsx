import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Info, Plus } from 'lucide-react'
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

const filterSelectClass =
  'w-full rounded border border-stone-300 bg-white px-2 py-1 text-xs outline-none focus:border-stone-400 dark:border-stone-700 dark:bg-stone-800 dark:focus:border-stone-500'

export function CampaignsPage() {
  const [data, setData] = useState<CampaignListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)

  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [refreshKey, setRefreshKey] = useState(0)

  // Debounce the search box so typing doesn't fire a request per keystroke --
  // the actual query param only updates once typing pauses.
  useEffect(() => {
    const id = setTimeout(() => setSearch(searchInput.trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [searchInput])

  // Any filter change invalidates the current page number -- e.g. page 5 of
  // an unfiltered list may not exist once a search/category/status narrows
  // the set.
  useEffect(() => {
    setPage(1)
  }, [search, category, status, pageSize])

  useEffect(() => {
    let cancelled = false
    listCampaigns({
      status: status || undefined,
      category: category || undefined,
      search: search || undefined,
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
  }, [search, category, status, page, pageSize, refreshKey])

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
  const hasFilters = Boolean(search || category || status)

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

      <div className="flex items-center justify-between text-sm text-stone-600 dark:text-stone-400">
        <span>Total: {total}</span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            aria-label="Previous page"
            className="grid place-items-center rounded-lg border border-stone-300 p-1.5 hover:bg-stone-100 disabled:opacity-40 disabled:hover:bg-transparent dark:border-stone-700 dark:hover:bg-stone-800"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="min-w-20 text-center">
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
            aria-label="Next page"
            className="grid place-items-center rounded-lg border border-stone-300 p-1.5 hover:bg-stone-100 disabled:opacity-40 disabled:hover:bg-transparent dark:border-stone-700 dark:hover:bg-stone-800"
          >
            <ChevronRight size={16} />
          </button>
        </div>
        <select
          value={pageSize}
          onChange={(e) => setPageSize(Number(e.target.value))}
          className="rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm outline-none focus:border-stone-400 dark:border-stone-700 dark:bg-stone-800 dark:focus:border-stone-500"
        >
          {PAGE_SIZE_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n} / page
            </option>
          ))}
        </select>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-stone-200 text-stone-500 dark:border-stone-700 dark:text-stone-400">
              <th className="py-2 pr-4">Headline</th>
              <th className="py-2 pr-4">Category</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2 pr-4">Budget</th>
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
                  placeholder="Filter"
                  className={filterSelectClass}
                />
              </th>
              <th className="py-1.5 pr-4 font-normal">
                <select value={category} onChange={(e) => setCategory(e.target.value)} className={filterSelectClass}>
                  <option value="">All</option>
                  {CAMPAIGN_CATEGORIES.map((c) => (
                    <option key={c} value={c}>
                      {categoryLabel(c)}
                    </option>
                  ))}
                </select>
              </th>
              <th className="py-1.5 pr-4 font-normal">
                <select value={status} onChange={(e) => setStatus(e.target.value)} className={filterSelectClass}>
                  <option value="">All</option>
                  {STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {categoryLabel(s)}
                    </option>
                  ))}
                </select>
              </th>
              <th className="py-1.5 pr-4" />
              <th className="py-1.5 pr-4" />
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
                  <td className="py-2 pr-4 font-medium">{c.headline}</td>
                  <td className="py-2 pr-4 text-stone-600 dark:text-stone-400">{categoryLabel(c.category)}</td>
                  <td className="py-2 pr-4">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="py-2 pr-4 text-stone-600 dark:text-stone-400">
                    ${c.budget_spent.toFixed(2)} / ${c.budget_total.toFixed(2)}
                  </td>
                  <td className="py-2 pr-4 text-stone-500 dark:text-stone-500">{c.review_reason ?? '—'}</td>
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
