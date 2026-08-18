import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, fetchRecommendationBatch } from '../api'
import type { FeedItem } from '../types'
import { FeedCard } from './FeedCard'
import { Spinner } from './Spinner'

// The backend caps batch_size at 50 -- using the max amortizes the one
// LLM re-rank call per batch across as many items as possible, minimizing
// total re-rank calls per scroll session.
const BATCH_SIZE = 50

export function Feed() {
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
      const batch = await fetchRecommendationBatch(BATCH_SIZE)
      setItems((prev) => {
        const seen = new Set(prev.map((i) => i.ad_id))
        return [...prev, ...batch.items.filter((i) => !seen.has(i.ad_id))]
      })
      if (batch.items.length === 0) setExhausted(true)
    } catch (err) {
      // A 404 here means retrieve_candidates ran dry (nothing eligible left) --
      // not a real error, just "nothing more to show right now."
      if (err instanceof ApiError && err.status === 404) {
        setExhausted(true)
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setLoading(false)
      loadingRef.current = false
    }
  }, [exhausted])

  useEffect(() => {
    loadMore()
  }, [loadMore])

  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    // rootMargin only expands the root's own bounds -- root defaults to the
    // top-level viewport, but the actual scrolling happens inside SimpleBar's
    // nested overflow:auto container, which clips the sentinel at its own
    // (unexpanded) edge before the margin ever applies. Pointing root at
    // that real scrolling ancestor is what makes rootMargin do anything here.
    const root = el.closest<HTMLElement>('.simplebar-content-wrapper')
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) loadMore()
      },
      { root, rootMargin: '2000px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
    // loading/items.length aren't read in here, but the sentinel div only
    // exists in the DOM once the initial-load early return below stops
    // firing -- loadMore's own reference doesn't change at that point, so
    // without these the observer would never actually get attached (it'd
    // bail out via `if (!el) return` on the one render where it runs).
  }, [loadMore, loading, items.length])

  function hideItem(adId: string) {
    setItems((prev) => prev.filter((i) => i.ad_id !== adId))
  }

  if (loading && items.length === 0) {
    // Fixed, spanning the full viewport height -- centering within flex-1
    // (the space left after the sticky restart bar above it) put this
    // slightly below true screen-center. left-56 matches the sidebar's
    // width (App.tsx), so it stays within the main content area instead of
    // centering across the whole window including the sidebar.
    return (
      <div className="fixed inset-y-0 left-56 right-0 flex items-center justify-center">
        <Spinner />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      {items.map((item) => (
        <FeedCard key={item.ad_id} item={item} onHidden={hideItem} />
      ))}
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      {!exhausted && <div ref={sentinelRef} className="h-0" />}
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
