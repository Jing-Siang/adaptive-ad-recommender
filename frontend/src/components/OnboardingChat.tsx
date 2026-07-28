import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowUp } from 'lucide-react'
import {
  logImpression,
  onboardingCheckpoint,
  sendReaction,
  streamOnboardingChat,
} from '../api'
import type { AdCandidate, ChatMessage, Reaction } from '../types'
import { ReactionButtons } from './ReactionButtons'

const OPENING_MESSAGE = "Hi! What are you into these days?"
const MAX_CHECKPOINT_ROUNDS = 3
const TEXTAREA_MAX_HEIGHT = 160 // px

interface DisplayTurn {
  role: 'user' | 'assistant'
  content: string
  candidates?: AdCandidate[]
}

function describeReactions(candidates: AdCandidate[], reactions: Record<string, Reaction>): string | null {
  const entries = candidates
    .filter((c) => reactions[c.ad_id])
    .map((c) => {
      const r = reactions[c.ad_id]
      const verb = r === 'like' ? 'liked' : r === 'dislike' ? "wasn't interested in" : 'was very interested in'
      return `${verb} "${c.headline}"`
    })
  if (entries.length === 0) return null
  return `(I ${entries.join(', ')}.)`
}

export function OnboardingChat({ userId, onFinish }: { userId: string; onFinish: () => void }) {
  const [apiMessages, setApiMessages] = useState<ChatMessage[]>([{ role: 'assistant', content: OPENING_MESSAGE }])
  const [displayTurns, setDisplayTurns] = useState<DisplayTurn[]>([{ role: 'assistant', content: OPENING_MESSAGE }])
  const [lastCandidates, setLastCandidates] = useState<AdCandidate[]>([])
  const [reactions, setReactions] = useState<Record<string, Reaction>>({})
  const [checkpointRounds, setCheckpointRounds] = useState(0)
  const [readyToFinish, setReadyToFinish] = useState(false)
  const [input, setInput] = useState('')
  const [streamingText, setStreamingText] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [displayTurns, streamingText])

  useEffect(() => () => abortRef.current?.abort(), [])

  useEffect(() => {
    if (!busy) textareaRef.current?.focus()
  }, [busy])

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, TEXTAREA_MAX_HEIGHT)}px`
    el.style.overflowY = el.scrollHeight > TEXTAREA_MAX_HEIGHT ? 'auto' : 'hidden'
  }, [input])

  async function handleReact(adId: string, reaction: Reaction) {
    const isUnchecking = reactions[adId] === reaction
    setReactions((r) => {
      if (!isUnchecking) return { ...r, [adId]: reaction }
      const { [adId]: _removed, ...rest } = r
      return rest
    })
    // Unchecking only clears local UI state -- there's no backend endpoint to
    // undo a reaction, so there's nothing to send in that case.
    if (isUnchecking) return
    try {
      await sendReaction(userId, adId, reaction)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleSubmit() {
    const reactionNote = describeReactions(lastCandidates, reactions)
    const text = input.trim()
    if (!text) return

    // The reaction note is sent to the model (so it has real signal to react
    // to) but never shown in the chat itself -- the user already saw/reacted
    // to the cards, restating it as a chat bubble would just be noise.
    const newMessages: ChatMessage[] = [...apiMessages]
    const newTurns: DisplayTurn[] = [...displayTurns]
    if (reactionNote) {
      newMessages.push({ role: 'user', content: reactionNote })
    }
    if (text) {
      newMessages.push({ role: 'user', content: text })
      newTurns.push({ role: 'user', content: text })
    }

    setApiMessages(newMessages)
    setDisplayTurns(newTurns)
    setInput('')
    setLastCandidates([])
    // Not clearing reactions here -- it's keyed by ad_id, and each round's
    // candidates get fresh ad_ids, so old entries never collide with a new
    // round's cards. Clearing it wiped the "selected" highlight off cards
    // from earlier turns the moment you submitted, even though the reaction
    // itself was already recorded.
    setBusy(true)
    setError(null)
    setStreamingText('')

    try {
      const checkpoint = await onboardingCheckpoint(userId, newMessages)

      // MAX_CHECKPOINT_ROUNDS is a client-only safety net -- the backend
      // has no concept of it, so checkpoint.ready_to_finish alone can't
      // reflect it. Deciding this before the chat call (rather than after,
      // via checkpointRounds state) keeps what's sent to /onboarding/chat
      // and what the UI shows in sync -- otherwise the round that crosses
      // the cap still went out as a normal turn (fresh question +
      // candidates, no idea it should've wrapped up), and the finish
      // button only appeared on the render after, sitting right below that
      // freshly-generated block.
      const roundsAfterThis = checkpoint.show_candidates ? checkpointRounds + 1 : checkpointRounds
      const readyToFinish = checkpoint.ready_to_finish || roundsAfterThis >= MAX_CHECKPOINT_ROUNDS

      abortRef.current = new AbortController()
      let fullReply = ''
      await streamOnboardingChat(
        newMessages,
        readyToFinish,
        (chunk) => {
          fullReply += chunk
          setStreamingText(fullReply)
        },
        abortRef.current.signal,
      )

      const showCandidatesThisTurn = checkpoint.show_candidates && !readyToFinish

      setApiMessages((msgs) => [...msgs, { role: 'assistant', content: fullReply }])
      setDisplayTurns((turns) => [
        ...turns,
        { role: 'assistant', content: fullReply, candidates: showCandidatesThisTurn ? checkpoint.candidates : undefined },
      ])
      setStreamingText(null)

      if (showCandidatesThisTurn) {
        setLastCandidates(checkpoint.candidates)
        setCheckpointRounds((n) => n + 1)
        checkpoint.candidates.forEach((c) => {
          logImpression(userId, c.ad_id).catch(() => {})
        })
      }
      setReadyToFinish(readyToFinish)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!busy && input.trim().length > 0) handleSubmit()
    }
  }

  const canFinish = readyToFinish || checkpointRounds >= MAX_CHECKPOINT_ROUNDS
  const canSubmit = !busy && input.trim().length > 0

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-6 pt-6">
      {/* px-4 (16px) matches the composer's rounded-2xl corner radius, so
          the message column lines up with where its straight edge starts
          rather than its curved corner. */}
      <div className="flex-1 space-y-5 px-4 pb-4">
        {displayTurns.map((turn, i) => (
          <div
            key={i}
            className={`${turn.role === 'user' ? 'flex justify-end' : ''} ${
              turn.role === 'user' && i > 0 ? 'mt-8' : ''
            }`}
          >
            {turn.role === 'user' ? (
              <div className="max-w-[85%] rounded-2xl bg-stone-200 px-4 py-2.5 leading-relaxed text-stone-900 dark:bg-stone-700 dark:text-stone-100">
                {turn.content}
              </div>
            ) : (
              <div className="max-w-full leading-relaxed text-stone-800 dark:text-stone-200">{turn.content}</div>
            )}
            {turn.candidates && turn.candidates.length > 0 && (
              // A visually distinct, muted "aside" box -- separate from the
              // conversation itself, so it reads as an optional suggestion
              // to glance at, not a second thing the user has to answer
              // alongside the actual question above.
              <div className="mt-3 rounded-xl bg-stone-100/70 p-3 dark:bg-stone-800/50">
                <p className="text-xs font-medium text-stone-500 dark:text-stone-400">
                  A few things you might like
                </p>
                <div className="mt-2 space-y-1.5">
                  {turn.candidates.map((c) => {
                    // Reference-equal to lastCandidates only for the round
                    // that hasn't been submitted yet -- once you move on,
                    // lastCandidates is reset/reassigned, so older rounds'
                    // reactions are locked in and can't be changed anymore.
                    const isActiveRound = turn.candidates === lastCandidates
                    return (
                      <div key={c.ad_id} className="rounded-lg bg-white p-2.5 dark:bg-stone-900">
                        <p className="text-sm font-medium">{c.headline}</p>
                        <p className="mt-0.5 text-xs text-stone-600 dark:text-stone-400">{c.description}</p>
                        <div className="mt-2">
                          <ReactionButtons
                            selected={reactions[c.ad_id] ?? null}
                            onReact={(r) => handleReact(c.ad_id, r)}
                            disabled={!isActiveRound}
                          />
                        </div>
                      </div>
                    )
                  })}
                </div>
                <p className="mt-2 text-xs text-stone-500 dark:text-stone-500">
                  React to what catches your eye, then tell me what you think.
                </p>
              </div>
            )}
          </div>
        ))}
        {streamingText !== null && (
          <div className="max-w-full leading-relaxed text-stone-800 dark:text-stone-200">{streamingText || '…'}</div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Sticky, not internally scrollable -- the page's own scroll (main,
          in App.tsx) is the only scroll container here, matching how the
          Claude chat app scrolls the whole conversation pane and just
          docks the composer to the bottom of it. */}
      <div className="sticky bottom-0">
        {/* Fades scrolling content out before it disappears under the solid
            bar below, instead of a hard cutoff. */}
        <div className="pointer-events-none h-6 bg-gradient-to-t from-stone-50 to-transparent dark:from-stone-900" />
        <div className="bg-stone-50 pb-6 dark:bg-stone-900">
          {error && <p className="pb-2 text-sm text-red-600 dark:text-red-400">{error}</p>}

          {canFinish ? (
            <button
              type="button"
              onClick={onFinish}
              className="w-full rounded-2xl bg-stone-900 py-3 text-sm font-medium text-white shadow-sm hover:bg-stone-700 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-stone-300"
            >
              Continue to your feed →
            </button>
          ) : (
            <div className="flex items-end gap-2 rounded-2xl border border-stone-300 bg-white py-2 pl-4 pr-2 shadow-sm dark:border-stone-700 dark:bg-stone-800">
              <textarea
                ref={textareaRef}
                rows={1}
                value={input}
                disabled={busy}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type a reply…"
                style={{ maxHeight: TEXTAREA_MAX_HEIGHT }}
                className="thin-scrollbar block max-h-40 flex-1 resize-none bg-transparent py-2 leading-relaxed outline-none"
              />
              {/* items-end on the row pins the button to the box's bottom edge,
                  which stays put in the viewport as the box grows upward --
                  it's a plain flex sibling now, not overlaid on the textarea,
                  so the textarea's own scrollbar never sits under it. */}
              <button
                type="button"
                disabled={!canSubmit}
                onClick={handleSubmit}
                aria-label="Send"
                className={`mb-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition ${
                  canSubmit
                    ? 'bg-stone-900 text-white hover:bg-stone-700 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-stone-300'
                    : 'bg-stone-200 text-stone-400 dark:bg-stone-700 dark:text-stone-500'
                }`}
              >
                <ArrowUp size={16} />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
