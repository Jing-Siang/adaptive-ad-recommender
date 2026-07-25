import { useEffect, useRef, useState } from 'react'
import { Flag, EyeOff, Info } from 'lucide-react'
import { doNotShowAgain, logImpression, sendReaction, sendReport } from '../api'
import type { FeedItem, Reaction, ReportCategory } from '../types'
import { ReactionButtons } from './ReactionButtons'
import { ReportModal } from './ReportModal'

export function FeedCard({
  item,
  userId,
  onHidden,
}: {
  item: FeedItem
  userId: string
  onHidden: (adId: string) => void
}) {
  const [reaction, setReaction] = useState<Reaction | null>(null)
  const [showReport, setShowReport] = useState(false)
  const [showWhy, setShowWhy] = useState(false)
  const [reported, setReported] = useState(false)
  const impressionLogged = useRef(false)
  const cardRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = cardRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !impressionLogged.current) {
          impressionLogged.current = true
          logImpression(userId, item.ad_id).catch(() => {})
        }
      },
      { threshold: 0.5 },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [userId, item.ad_id])

  async function handleReact(r: Reaction) {
    setReaction(r)
    try {
      await sendReaction(userId, item.ad_id, r)
    } catch {
      // best-effort; reaction UI state already reflects the attempt
    }
  }

  async function handleReport(category: ReportCategory, reason?: string) {
    try {
      await sendReport(userId, item.ad_id, category, reason)
    } finally {
      setShowReport(false)
      setReported(true)
    }
  }

  async function handleDoNotShow() {
    try {
      await doNotShowAgain(userId, item.ad_id)
    } finally {
      onHidden(item.ad_id)
    }
  }

  if (reported) {
    return (
      <div ref={cardRef} className="rounded border border-stone-200 p-4 text-sm text-stone-500 dark:border-stone-700 dark:text-stone-500">
        Thanks — this ad has been reported and won't be emphasized here.
      </div>
    )
  }

  return (
    <div ref={cardRef} className="rounded border border-stone-200 p-4 dark:border-stone-700">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="font-semibold">{item.headline}</h3>
        <span className="text-xs text-stone-500 dark:text-stone-500">{item.category}</span>
      </div>
      <p className="mt-1 text-sm text-stone-700 dark:text-stone-300">{item.description}</p>
      {item.price !== null && <p className="mt-1 text-sm font-medium">${item.price.toFixed(2)}</p>}

      <div className="mt-3 flex items-center justify-between">
        <ReactionButtons selected={reaction} onReact={handleReact} />
        <div className="flex items-center gap-3 text-stone-400 dark:text-stone-600">
          <button type="button" title="Why am I seeing this?" onClick={() => setShowWhy((v) => !v)} className="hover:text-stone-700 dark:hover:text-stone-300">
            <Info size={16} />
          </button>
          <button type="button" title="Report" onClick={() => setShowReport(true)} className="hover:text-red-600 dark:hover:text-red-400">
            <Flag size={16} />
          </button>
          <button type="button" title="Don't show again" onClick={handleDoNotShow} className="hover:text-stone-700 dark:hover:text-stone-300">
            <EyeOff size={16} />
          </button>
        </div>
      </div>

      {showWhy && (
        <div className="mt-3 rounded bg-stone-50 p-2 text-xs text-stone-600 dark:bg-stone-800 dark:text-stone-400">
          <p>
            Relevance score: <span className="font-medium">{item.relevance_score.toFixed(2)}</span> · Similarity:{' '}
            <span className="font-medium">{item.similarity_score.toFixed(2)}</span>
          </p>
          <p className="mt-1">{item.justification}</p>
        </div>
      )}

      {showReport && <ReportModal onSubmit={handleReport} onClose={() => setShowReport(false)} />}
    </div>
  )
}
