import { useEffect, useRef, useState } from 'react'
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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [displayTurns, streamingText])

  useEffect(() => () => abortRef.current?.abort(), [])

  async function handleReact(adId: string, reaction: Reaction) {
    setReactions((r) => ({ ...r, [adId]: reaction }))
    try {
      await sendReaction(userId, adId, reaction)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleSubmit() {
    const reactionNote = describeReactions(lastCandidates, reactions)
    const text = input.trim()
    if (!reactionNote && !text) return

    const newMessages: ChatMessage[] = [...apiMessages]
    const newTurns: DisplayTurn[] = [...displayTurns]
    if (reactionNote) {
      newMessages.push({ role: 'user', content: reactionNote })
      newTurns.push({ role: 'user', content: reactionNote })
    }
    if (text) {
      newMessages.push({ role: 'user', content: text })
      newTurns.push({ role: 'user', content: text })
    }

    setApiMessages(newMessages)
    setDisplayTurns(newTurns)
    setInput('')
    setLastCandidates([])
    setReactions({})
    setBusy(true)
    setError(null)
    setStreamingText('')

    try {
      const checkpoint = await onboardingCheckpoint(userId, newMessages)

      abortRef.current = new AbortController()
      let fullReply = ''
      await streamOnboardingChat(newMessages, checkpoint.show_candidates, (chunk) => {
        fullReply += chunk
        setStreamingText(fullReply)
      }, abortRef.current.signal)

      setApiMessages((msgs) => [...msgs, { role: 'assistant', content: fullReply }])
      setDisplayTurns((turns) => [
        ...turns,
        { role: 'assistant', content: fullReply, candidates: checkpoint.show_candidates ? checkpoint.candidates : undefined },
      ])
      setStreamingText(null)

      if (checkpoint.show_candidates) {
        setLastCandidates(checkpoint.candidates)
        setCheckpointRounds((n) => n + 1)
        checkpoint.candidates.forEach((c) => {
          logImpression(userId, c.ad_id).catch(() => {})
        })
      }
      setReadyToFinish(checkpoint.ready_to_finish)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const canFinish = readyToFinish || checkpointRounds >= MAX_CHECKPOINT_ROUNDS

  return (
    <div className="mx-auto flex h-[calc(100svh-56px)] max-w-2xl flex-col p-6">
      <div className="flex-1 space-y-4 overflow-y-auto">
        {displayTurns.map((turn, i) => (
          <div key={i} className={turn.role === 'user' ? 'text-right' : 'text-left'}>
            <div
              className={`inline-block max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                turn.role === 'user'
                  ? 'bg-indigo-600 text-white'
                  : 'bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-100'
              }`}
            >
              {turn.content}
            </div>
            {turn.candidates && turn.candidates.length > 0 && (
              <div className="mt-2 space-y-2 text-left">
                {turn.candidates.map((c) => (
                  <div key={c.ad_id} className="rounded border border-slate-200 p-3 dark:border-slate-800">
                    <p className="text-sm font-medium">{c.headline}</p>
                    <p className="text-xs text-slate-600 dark:text-slate-400">{c.description}</p>
                    <div className="mt-2">
                      <ReactionButtons
                        selected={reactions[c.ad_id] ?? null}
                        onReact={(r) => handleReact(c.ad_id, r)}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {streamingText !== null && (
          <div className="text-left">
            <div className="inline-block max-w-[85%] rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-900 dark:bg-slate-800 dark:text-slate-100">
              {streamingText || '…'}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}

      {canFinish && (
        <button
          type="button"
          onClick={onFinish}
          className="mt-3 rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
        >
          Continue to your feed →
        </button>
      )}

      <div className="mt-3 flex gap-2">
        <input
          value={input}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !busy) handleSubmit()
          }}
          placeholder="Type a reply…"
          className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
        />
        <button
          type="button"
          disabled={busy}
          onClick={handleSubmit}
          className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          Send
        </button>
      </div>
    </div>
  )
}
