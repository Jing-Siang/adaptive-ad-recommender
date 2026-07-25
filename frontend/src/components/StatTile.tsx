export function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-stone-200 bg-white p-4 dark:border-stone-700 dark:bg-stone-800">
      <p className="text-sm text-stone-500 dark:text-stone-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-stone-900 dark:text-stone-100">{value}</p>
    </div>
  )
}
