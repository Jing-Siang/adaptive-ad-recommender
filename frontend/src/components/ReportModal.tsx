import { useState } from 'react'
import type { ReportCategory } from '../types'

const CATEGORIES: { value: ReportCategory; label: string }[] = [
  { value: 'misleading', label: 'Misleading' },
  { value: 'offensive', label: 'Offensive' },
  { value: 'irrelevant', label: 'Irrelevant' },
  { value: 'spam', label: 'Spam' },
  { value: 'other', label: 'Other' },
]

export function ReportModal({
  onSubmit,
  onClose,
}: {
  onSubmit: (category: ReportCategory, reason?: string) => void
  onClose: () => void
}) {
  const [category, setCategory] = useState<ReportCategory>('misleading')
  const [reason, setReason] = useState('')
  const needsReason = category === 'other'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded bg-white p-4 dark:bg-stone-800">
        <h3 className="font-semibold">Report this ad</h3>
        <div className="mt-3 space-y-2">
          {CATEGORIES.map((c) => (
            <label key={c.value} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="report-category"
                checked={category === c.value}
                onChange={() => setCategory(c.value)}
              />
              {c.label}
            </label>
          ))}
        </div>
        {needsReason && (
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Tell us more…"
            rows={2}
            className="thin-scrollbar mt-2 min-h-16 max-h-40 w-full overflow-y-auto rounded border border-stone-300 px-3 py-2 text-sm dark:border-stone-700 dark:bg-stone-800"
          />
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded px-3 py-1.5 text-sm text-stone-600 hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-800"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={needsReason && !reason.trim()}
            onClick={() => onSubmit(category, reason.trim() || undefined)}
            className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
          >
            Submit report
          </button>
        </div>
      </div>
    </div>
  )
}
