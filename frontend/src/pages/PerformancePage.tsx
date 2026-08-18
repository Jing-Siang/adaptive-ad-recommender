import { useEffect, useState } from 'react'
import { Info } from 'lucide-react'
import { fetchPerformance } from '../api'
import type { PerformanceResponse } from '../types'
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

export function PerformancePage() {
  const [data, setData] = useState<PerformanceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchPerformance()
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

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

  const { totals, trend, campaigns } = data

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

      <div>
        <h2 className="text-lg font-semibold">Per-campaign breakdown</h2>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-stone-200 text-stone-500 dark:border-stone-700 dark:text-stone-400">
                <th className="py-2 pr-4">Campaign</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Impr.</th>
                <th className="py-2 pr-4">Likes</th>
                <th className="py-2 pr-4">Dislikes</th>
                <th className="py-2 pr-4">Conv.</th>
                <th className="py-2 pr-4">Reports</th>
                <th className="py-2 pr-4">CTR</th>
                <th className="py-2 pr-4">Spend</th>
              </tr>
            </thead>
            <tbody className="[font-variant-numeric:tabular-nums]">
              {campaigns.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-6 text-center text-stone-500 dark:text-stone-500">
                    No campaigns yet.
                  </td>
                </tr>
              ) : (
                [...campaigns]
                  .sort((a, b) => b.impressions - a.impressions)
                  .map((c) => (
                    <tr key={c.campaign_id} className="border-b border-stone-100 dark:border-stone-900">
                      <td className="py-2 pr-4 font-medium">{c.headline}</td>
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
    </div>
  )
}
