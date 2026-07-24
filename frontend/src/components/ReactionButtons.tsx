import { Heart, Star, ThumbsDown } from 'lucide-react'
import type { Reaction } from '../types'

const OPTIONS: { reaction: Reaction; label: string; Icon: typeof Heart }[] = [
  { reaction: 'like', label: 'Like', Icon: Heart },
  { reaction: 'dislike', label: 'Dislike', Icon: ThumbsDown },
  { reaction: 'interested', label: 'Interested', Icon: Star },
]

export function ReactionButtons({
  selected,
  onReact,
}: {
  selected: Reaction | null
  onReact: (reaction: Reaction) => void
}) {
  return (
    <div className="flex gap-2">
      {OPTIONS.map(({ reaction, label, Icon }) => (
        <button
          key={reaction}
          type="button"
          onClick={() => onReact(reaction)}
          disabled={selected !== null}
          className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition disabled:cursor-default ${
            selected === reaction
              ? 'border-indigo-500 bg-indigo-50 text-indigo-700 dark:border-indigo-400 dark:bg-indigo-950/40 dark:text-indigo-300'
              : selected !== null
                ? 'border-slate-200 text-slate-400 dark:border-slate-800 dark:text-slate-600'
                : 'border-slate-300 text-slate-700 hover:border-indigo-400 hover:text-indigo-600 dark:border-slate-700 dark:text-slate-300'
          }`}
        >
          <Icon size={14} />
          {label}
        </button>
      ))}
    </div>
  )
}
