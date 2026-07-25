const STATUS_STYLES: Record<string, string> = {
  pending_review: 'bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300',
  needs_review: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  rejected: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
  completed: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',
}

export function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? 'bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300'
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${style}`}>
      {status.replace('_', ' ')}
    </span>
  )
}
