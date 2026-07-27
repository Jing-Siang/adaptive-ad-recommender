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
  disabled = false,
}: {
  selected: Reaction | null
  onReact: (reaction: Reaction) => void
  disabled?: boolean
}) {
  return (
    <div className="flex gap-2">
      {OPTIONS.map(({ reaction, label, Icon }) => (
        <button
          key={reaction}
          type="button"
          disabled={disabled}
          onClick={() => onReact(reaction)}
          className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
            disabled
              ? selected === reaction
                ? 'border-stone-400 bg-stone-300 text-stone-600 dark:border-stone-600 dark:bg-stone-700 dark:text-stone-400'
                : 'cursor-not-allowed border-stone-200 text-stone-300 dark:border-stone-800 dark:text-stone-700'
              : selected === reaction
                ? 'border-stone-900 bg-stone-900 text-white dark:border-stone-100 dark:bg-stone-100 dark:text-stone-900'
                : 'border-stone-300 text-stone-700 hover:border-stone-900 hover:text-stone-900 dark:border-stone-700 dark:text-stone-300 dark:hover:border-stone-100 dark:hover:text-stone-100'
          }`}
        >
          <Icon size={14} />
          {label}
        </button>
      ))}
    </div>
  )
}
