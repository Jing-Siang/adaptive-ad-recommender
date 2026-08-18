import { useEffect, useState } from 'react'
import { Info } from 'lucide-react'
import { listCampaigns, moderateCampaign } from '../api'
import type { CampaignResponse } from '../types'
import { categoryLabel } from '../categories'
import { Spinner } from '../components/Spinner'
import { Tooltip } from '../components/Tooltip'

export function ModeratorPage() {
  const [campaigns, setCampaigns] = useState<CampaignResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reasons, setReasons] = useState<Record<number, string>>({})
  const [submittingId, setSubmittingId] = useState<number | null>(null)

  async function refresh() {
    try {
      const data = await listCampaigns({ status: 'needs_review', pageSize: 100 })
      setCampaigns(data.items.sort((a, b) => a.id - b.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function handleDecision(id: number, outcome: 'approved' | 'rejected') {
    const reason = reasons[id]?.trim()
    if (!reason) {
      setError('A reason is required before approving or rejecting.')
      return
    }
    setSubmittingId(id)
    setError(null)
    try {
      await moderateCampaign(id, { outcome, reason })
      setCampaigns((cs) => cs.filter((c) => c.id !== id))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmittingId(null)
    }
  }

  if (loading) {
    return (
      <div className="fixed inset-y-0 left-56 right-0 flex items-center justify-center">
        <Spinner />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex items-center gap-1.5">
        <h1 className="mt-3 text-2xl font-semibold">Moderator Queue</h1>
        <Tooltip
          side="right"
          content="Campaigns the AI reviewer flagged for a human decision. Your account name is recorded with the decision for the audit trail."
        >
          <span className="mt-3.5 text-stone-500 dark:text-stone-400">
            <Info size={16} />
          </span>
        </Tooltip>
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      {campaigns.length === 0 ? (
        <p className="text-sm text-stone-600 dark:text-stone-400">Queue is empty. Nothing needs review.</p>
      ) : (
        <ul className="space-y-4">
          {campaigns.map((c) => (
            <li key={c.id} className="rounded border border-stone-200 p-4 dark:border-stone-700">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="font-semibold">{c.headline}</h2>
                <span className="text-xs text-stone-500 dark:text-stone-500">{categoryLabel(c.category)}</span>
              </div>
              <p className="mt-1 text-sm text-stone-700 dark:text-stone-300">{c.description}</p>
              <p className="mt-2 text-xs text-stone-500 dark:text-stone-500">
                Budget: ${c.budget_total.toFixed(2)} · {c.start_date} → {c.end_date}
              </p>
              {c.review_reason && (
                <p className="mt-2 rounded bg-amber-50 p-2 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                  <span className="font-medium">Why it was flagged:</span> {c.review_reason}
                </p>
              )}
              {c.research_notes && (
                <p className="mt-2 rounded bg-stone-50 p-2 text-sm text-stone-700 dark:bg-stone-800 dark:text-stone-300">
                  <span className="font-medium">Research notes:</span> {c.research_notes}
                </p>
              )}

              <label className="mt-3 flex flex-col gap-1 text-sm">
                Decision reason
                <textarea
                  rows={2}
                  value={reasons[c.id] ?? ''}
                  onChange={(e) => setReasons((r) => ({ ...r, [c.id]: e.target.value }))}
                  className="rounded border border-stone-300 px-3 py-2 dark:border-stone-700 dark:bg-stone-800"
                />
              </label>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  disabled={submittingId === c.id}
                  onClick={() => handleDecision(c.id, 'approved')}
                  className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
                >
                  Approve
                </button>
                <button
                  type="button"
                  disabled={submittingId === c.id}
                  onClick={() => handleDecision(c.id, 'rejected')}
                  className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
                >
                  Reject
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
