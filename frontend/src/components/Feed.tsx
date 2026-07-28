import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchRecommendationBatch } from '../api'
import type { FeedItem } from '../types'
import { FeedCard } from './FeedCard'
import { Spinner } from './Spinner'

// The backend caps batch_size at 50 -- using the max amortizes the one
// LLM re-rank call per batch across as many items as possible, minimizing
// total re-rank calls per scroll session.
const BATCH_SIZE = 50

export function Feed({ userId }: { userId: string }) {
  const [items, setItems] = useState<FeedItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [exhausted, setExhausted] = useState(false)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const loadingRef = useRef(false)

  const loadMore = useCallback(async () => {
    if (loadingRef.current || exhausted) return
    loadingRef.current = true
    setLoading(true)
    try {
      const batch = await fetchRecommendationBatch(userId, BATCH_SIZE)
      setItems((prev) => {
        const seen = new Set(prev.map((i) => i.ad_id))
        return [...prev, ...batch.items.filter((i) => !seen.has(i.ad_id))]
      })
      if (batch.items.length === 0) setExhausted(true)
    } catch (err) {
      // A 404 here means retrieve_candidates ran dry (nothing eligible left) --
      // not a real error, just "nothing more to show right now."
      if (err instanceof Error && err.message.includes('404')) {
        setExhausted(true)
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setLoading(false)
      loadingRef.current = false
    }
  }, [userId, exhausted])

  useEffect(() => {
    loadMore()
  }, [loadMore])

  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) loadMore()
      },
      { rootMargin: '400px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [loadMore])

  function hideItem(adId: string) {
    setItems((prev) => prev.filter((i) => i.ad_id !== adId))
  }

  if (loading && items.length === 0) {
    // flex-1, not a vh guess -- fills whatever space this actually has in
    // its parent's flex column (which already excludes the sticky restart
    // bar above it, since that's a sibling row, not part of this box), so
    // it centers correctly no matter that header's real height.
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      {items.map((item) => (
        <FeedCard key={item.ad_id} item={item} userId={userId} onHidden={hideItem} />
      ))}
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      {!exhausted && <div ref={sentinelRef} className="h-4" />}
      {loading && items.length > 0 && (
        <div className="flex justify-center py-4">
          <Spinner />
        </div>
      )}
      {exhausted && items.length === 0 && (
        <p className="text-center text-sm text-stone-500 dark:text-stone-500">Nothing to show right now.</p>
      )}
      {exhausted && items.length > 0 && (
        <p className="text-center text-sm text-stone-500 dark:text-stone-500">You're all caught up.</p>
      )}
    </div>
  )
}
