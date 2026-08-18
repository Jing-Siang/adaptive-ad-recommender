import { useEffect, useState } from 'react'
import { Plus } from 'lucide-react'
import { listCampaigns } from '../api'
import type { CampaignResponse } from '../types'
import { categoryLabel } from '../categories'
import { StatusBadge } from '../components/StatusBadge'
import { CampaignFormModal } from '../components/CampaignFormModal'
import { Spinner } from '../components/Spinner'

export function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<CampaignResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)

  async function refresh() {
    try {
      const data = await listCampaigns()
      setCampaigns(data.sort((a, b) => b.id - a.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  // Poll while anything is still pending_review, so the async review
  // outcome shows up without a manual refresh.
  useEffect(() => {
    const hasPending = campaigns.some((c) => c.status === 'pending_review')
    if (!hasPending) return
    const id = setInterval(refresh, 3000)
    return () => clearInterval(id)
  }, [campaigns])

  if (loading) {
    return (
      <div className="fixed inset-y-0 left-56 right-0 flex items-center justify-center">
        <Spinner />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Campaigns</h1>
          <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">All submitted campaigns and their status.</p>
        </div>
        <button
          type="button"
          onClick={() => setShowModal(true)}
          className="flex items-center gap-1.5 rounded-lg border border-stone-300 bg-stone-50 px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-100 dark:border-stone-600 dark:bg-stone-800 dark:text-stone-200 dark:hover:bg-stone-700"
        >
          <Plus size={16} />
          New campaign
        </button>
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      {campaigns.length === 0 ? (
        <p className="text-sm text-stone-600 dark:text-stone-400">No campaigns yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-stone-200 text-stone-500 dark:border-stone-700 dark:text-stone-400">
                <th className="py-2 pr-4">Headline</th>
                <th className="py-2 pr-4">Category</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Budget</th>
                <th className="py-2 pr-4">Note</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
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
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showModal && (
        <CampaignFormModal
          onClose={() => setShowModal(false)}
          onCreated={() => {
            setShowModal(false)
            refresh()
          }}
        />
      )}
    </div>
  )
}
