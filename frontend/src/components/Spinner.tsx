export function Spinner({ className = '' }: { className?: string }) {
  return (
    <div
      role="status"
      aria-label="Loading"
      className={`flex items-baseline gap-2 text-sm text-stone-500 dark:text-stone-400 ${className}`}
    >
      <span>Loading</span>
      <span className="inline-flex items-baseline gap-1">
        <span className="h-1.5 w-1.5 animate-bounce-jump rounded-full bg-current [animation-delay:-0.3s]" />
        <span className="h-1.5 w-1.5 animate-bounce-jump rounded-full bg-current [animation-delay:-0.15s]" />
        <span className="h-1.5 w-1.5 animate-bounce-jump rounded-full bg-current" />
      </span>
    </div>
  )
}
