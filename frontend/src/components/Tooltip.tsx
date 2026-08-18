import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import type { ReactNode } from 'react'

export function Tooltip({
  content,
  children,
  side = 'top',
}: {
  content: string
  children: ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
}) {
  return (
    <TooltipPrimitive.Provider delayDuration={200}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            side={side}
            sideOffset={6}
            className="max-w-xs rounded-lg bg-stone-900 px-3 py-1.5 text-xs leading-relaxed text-stone-50 shadow-lg dark:bg-stone-100 dark:text-stone-900"
          >
            {content}
            <TooltipPrimitive.Arrow className="fill-stone-900 dark:fill-stone-100" />
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  )
}
