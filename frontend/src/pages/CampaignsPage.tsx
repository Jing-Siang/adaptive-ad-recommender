import { useEffect, useState, type SubmitEvent } from 'react'
import { createCampaign, listCampaigns } from '../api'
import type { CampaignResponse } from '../types'
import { StatusBadge } from '../components/StatusBadge'

const DEFAULT_ADVERTISER = 'Demo Advertiser'

const EMPTY_FORM = {
  advertiser_name: DEFAULT_ADVERTISER,
  headline: '',
  description: '',
  category: '',
  objective: 'conversions',
  budget_total: '100',
  start_date: new Date().toISOString().slice(0, 10),
  end_date: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
}

export function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<CampaignResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [form, setForm] = useState(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  async function handleSubmit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await createCampaign({
        advertiser_name: form.advertiser_name,
        headline: form.headline,
        description: form.description,
        category: form.category,
        objective: form.objective,
        budget_total: Number(form.budget_total),
        start_date: form.start_date,
        end_date: form.end_date,
      })
      setForm({ ...EMPTY_FORM, advertiser_name: form.advertiser_name })
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  function update<K extends keyof typeof EMPTY_FORM>(key: K, value: (typeof EMPTY_FORM)[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Submit a Campaign</h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Submits with status=pending_review; an AI reviewer picks it up asynchronously within a few seconds.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          Advertiser name
          <input
            required
            value={form.advertiser_name}
            onChange={(e) => update('advertiser_name', e.target.value)}
            className="rounded border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          Headline
          <input
            required
            maxLength={200}
            value={form.headline}
            onChange={(e) => update('headline', e.target.value)}
            className="rounded border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          Description
          <textarea
            required
            maxLength={1000}
            rows={3}
            value={form.description}
            onChange={(e) => update('description', e.target.value)}
            className="rounded border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Category
          <input
            required
            placeholder="e.g. home_repair"
            value={form.category}
            onChange={(e) => update('category', e.target.value)}
            className="rounded border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Objective
          <input
            required
            placeholder="conversions / awareness"
            value={form.objective}
            onChange={(e) => update('objective', e.target.value)}
            className="rounded border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Budget total ($)
          <input
            required
            type="number"
            min="1"
            step="0.01"
            value={form.budget_total}
            onChange={(e) => update('budget_total', e.target.value)}
            className="rounded border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
          />
        </label>
        <div className="grid grid-cols-2 gap-4">
          <label className="flex flex-col gap-1 text-sm">
            Start date
            <input
              required
              type="date"
              value={form.start_date}
              onChange={(e) => update('start_date', e.target.value)}
              className="rounded border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            End date
            <input
              required
              type="date"
              value={form.end_date}
              onChange={(e) => update('end_date', e.target.value)}
              className="rounded border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
            />
          </label>
        </div>
        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={submitting}
            className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {submitting ? 'Submitting…' : 'Submit campaign'}
          </button>
        </div>
      </form>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div>
        <h2 className="text-lg font-semibold">All campaigns</h2>
        {loading ? (
          <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">Loading…</p>
        ) : campaigns.length === 0 ? (
          <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">No campaigns yet.</p>
        ) : (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500 dark:border-slate-800 dark:text-slate-400">
                  <th className="py-2 pr-4">Headline</th>
                  <th className="py-2 pr-4">Category</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Budget</th>
                  <th className="py-2 pr-4">Note</th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.id} className="border-b border-slate-100 dark:border-slate-900">
                    <td className="py-2 pr-4 font-medium">{c.headline}</td>
                    <td className="py-2 pr-4 text-slate-600 dark:text-slate-400">{c.category}</td>
                    <td className="py-2 pr-4">
                      <StatusBadge status={c.status} />
                    </td>
                    <td className="py-2 pr-4 text-slate-600 dark:text-slate-400">
                      ${c.budget_spent.toFixed(2)} / ${c.budget_total.toFixed(2)}
                    </td>
                    <td className="py-2 pr-4 text-slate-500 dark:text-slate-500">{c.review_reason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
