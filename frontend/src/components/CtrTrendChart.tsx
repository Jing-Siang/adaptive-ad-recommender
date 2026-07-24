import { useMemo, useState, type PointerEvent } from 'react'
import type { PerformanceTrendPoint } from '../types'

const WIDTH = 640
const HEIGHT = 220
const PADDING = { top: 16, right: 16, bottom: 28, left: 44 }
const PLOT_W = WIDTH - PADDING.left - PADDING.right
const PLOT_H = HEIGHT - PADDING.top - PADDING.bottom

function formatPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

function formatDate(d: string): string {
  const date = new Date(`${d}T00:00:00`)
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function CtrTrendChart({ data }: { data: PerformanceTrendPoint[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)
  const [showTable, setShowTable] = useState(false)

  const { points, yTicks, maxCtr } = useMemo(() => {
    const maxRaw = Math.max(0.01, ...data.map((d) => d.ctr))
    // Round the axis max up to a clean-ish step (nearest 0.01 above the data max).
    const max = Math.ceil(maxRaw * 100) / 100 + 0.01
    const xStep = data.length > 1 ? PLOT_W / (data.length - 1) : 0
    const pts = data.map((d, i) => ({
      x: PADDING.left + (data.length > 1 ? i * xStep : PLOT_W / 2),
      y: PADDING.top + PLOT_H * (1 - d.ctr / max),
      point: d,
    }))
    const ticks = [0, max / 2, max]
    return { points: pts, yTicks: ticks, maxCtr: max }
  }, [data])

  if (data.length === 0) {
    return <p className="text-sm text-slate-600 dark:text-slate-400">No activity yet.</p>
  }

  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
  const last = points[points.length - 1]

  function handlePointerMove(e: PointerEvent<SVGRectElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const relX = ((e.clientX - rect.left) / rect.width) * WIDTH
    let nearest = 0
    let best = Infinity
    points.forEach((p, i) => {
      const d = Math.abs(p.x - relX)
      if (d < best) {
        best = d
        nearest = i
      }
    })
    setHoverIndex(nearest)
  }

  const hovered = hoverIndex !== null ? points[hoverIndex] : null

  return (
    <div className="viz-root">
      <style>{`
        .viz-root {
          --surface-1: #fcfcfb;
          --text-secondary: #52514e;
          --muted: #898781;
          --gridline: #e1e0d9;
          --series-1: #2a78d6;
        }
        @media (prefers-color-scheme: dark) {
          .viz-root {
            --surface-1: #1a1a19;
            --text-secondary: #c3c2b7;
            --muted: #898781;
            --gridline: #2c2c2a;
            --series-1: #3987e5;
          }
        }
      `}</style>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-sm text-slate-500 dark:text-slate-500">
          Rolling CTR by day (conversions ÷ impressions)
        </p>
        <button
          type="button"
          onClick={() => setShowTable((v) => !v)}
          className="text-xs text-indigo-600 hover:underline dark:text-indigo-400"
        >
          {showTable ? 'Show chart' : 'Show table'}
        </button>
      </div>

      {showTable ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-slate-500 dark:border-slate-800 dark:text-slate-400">
                <th className="py-1.5 pr-4">Date</th>
                <th className="py-1.5 pr-4">Impressions</th>
                <th className="py-1.5 pr-4">Conversions</th>
                <th className="py-1.5 pr-4">CTR</th>
              </tr>
            </thead>
            <tbody className="[font-variant-numeric:tabular-nums]">
              {data.map((d) => (
                <tr key={d.date} className="border-b border-slate-100 dark:border-slate-900">
                  <td className="py-1.5 pr-4">{d.date}</td>
                  <td className="py-1.5 pr-4">{d.impressions}</td>
                  <td className="py-1.5 pr-4">{d.conversions}</td>
                  <td className="py-1.5 pr-4">{formatPct(d.ctr)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" style={{ background: 'var(--surface-1)' }}>
          {yTicks.map((t) => {
            const y = PADDING.top + PLOT_H * (1 - t / maxCtr)
            return (
              <g key={t}>
                <line
                  x1={PADDING.left}
                  x2={WIDTH - PADDING.right}
                  y1={y}
                  y2={y}
                  stroke="var(--gridline)"
                  strokeWidth={1}
                />
                <text x={PADDING.left - 8} y={y + 4} textAnchor="end" fontSize={11} fill="var(--muted)">
                  {formatPct(t)}
                </text>
              </g>
            )
          })}

          <path d={linePath} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

          <circle cx={last.x} cy={last.y} r={4} fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={2} />
          <text x={last.x - 6} y={last.y - 10} textAnchor="end" fontSize={12} fill="var(--text-secondary)">
            {formatPct(last.point.ctr)}
          </text>

          {points
            .filter((_, i) => i === 0 || i === points.length - 1 || i % Math.ceil(points.length / 5) === 0)
            .map((p) => (
              <text
                key={p.point.date}
                x={p.x}
                y={HEIGHT - 8}
                textAnchor="middle"
                fontSize={11}
                fill="var(--muted)"
              >
                {formatDate(p.point.date)}
              </text>
            ))}

          {hovered && (
            <line
              x1={hovered.x}
              x2={hovered.x}
              y1={PADDING.top}
              y2={PADDING.top + PLOT_H}
              stroke="var(--muted)"
              strokeWidth={1}
            />
          )}
          {hovered && (
            <circle cx={hovered.x} cy={hovered.y} r={4} fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={2} />
          )}

          <rect
            x={PADDING.left}
            y={PADDING.top}
            width={PLOT_W}
            height={PLOT_H}
            fill="transparent"
            onPointerMove={handlePointerMove}
            onPointerLeave={() => setHoverIndex(null)}
          />
        </svg>
      )}

      {hovered && (
        <div className="mt-1 inline-block rounded border border-slate-200 bg-white px-2 py-1 text-xs shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <span className="font-medium text-slate-900 dark:text-slate-100">{formatPct(hovered.point.ctr)}</span>{' '}
          <span className="text-slate-500 dark:text-slate-500">
            on {formatDate(hovered.point.date)} · {hovered.point.impressions} impressions, {hovered.point.conversions}{' '}
            conversions
          </span>
        </div>
      )}
    </div>
  )
}
