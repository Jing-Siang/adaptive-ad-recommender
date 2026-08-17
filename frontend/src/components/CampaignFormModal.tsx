import { useState, type SubmitEvent } from 'react'
import { createCampaign } from '../api'

const EMPTY_FORM = {
  headline: '',
  description: '',
  category: '',
  objective: 'conversions',
  budget_total: '100',
  start_date: new Date().toISOString().slice(0, 10),
  end_date: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
}

export function CampaignFormModal({ onCreated, onClose }: { onCreated: () => void; onClose: () => void }) {
  const [form, setForm] = useState(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function update<K extends keyof typeof EMPTY_FORM>(key: K, value: (typeof EMPTY_FORM)[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  async function handleSubmit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await createCampaign({
        headline: form.headline,
        description: form.description,
        category: form.category,
        objective: form.objective,
        budget_total: Number(form.budget_total),
        start_date: form.start_date,
        end_date: form.end_date,
      })
      onCreated()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setSubmitting(false)
    }
  }

  const inputClass = 'rounded border border-stone-300 px-3 py-2 dark:border-stone-700 dark:bg-stone-800'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white p-6 dark:bg-stone-800"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold">New campaign</h3>
        <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
          Submits with status=pending_review; an AI reviewer picks it up asynchronously within a few seconds.
        </p>

        <form onSubmit={handleSubmit} className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm sm:col-span-2">
            Headline
            <input
              required
              maxLength={200}
              value={form.headline}
              onChange={(e) => update('headline', e.target.value)}
              className={inputClass}
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
              className={inputClass}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Category
            <input
              required
              placeholder="e.g. home_repair"
              value={form.category}
              onChange={(e) => update('category', e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Objective
            <input
              required
              placeholder="conversions / awareness"
              value={form.objective}
              onChange={(e) => update('objective', e.target.value)}
              className={inputClass}
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
              className={inputClass}
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
                className={inputClass}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              End date
              <input
                required
                type="date"
                value={form.end_date}
                onChange={(e) => update('end_date', e.target.value)}
                className={inputClass}
              />
            </label>
          </div>

          {error && <p className="text-sm text-red-600 dark:text-red-400 sm:col-span-2">{error}</p>}

          <div className="flex justify-end gap-2 sm:col-span-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded px-3 py-2 text-sm text-stone-600 hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-700"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="rounded bg-stone-900 px-4 py-2 text-sm font-medium text-white hover:bg-stone-700 disabled:opacity-50 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-stone-300"
            >
              {submitting ? 'Submitting…' : 'Submit campaign'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
