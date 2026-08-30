"use client"

import * as React from "react"
import * as TabsPrimitive from "@radix-ui/react-tabs"

import { cn } from "@/lib/utils"

/**
 * Maximum-compatibility tab components.
 *
 * The default shadcn styling relied on a combination of features that break
 * on some mobile browsers (grid tracks sized `minmax(0,1fr)` squeezing
 * `white-space: nowrap` labels, `height: calc(100% - 1px)` inside an
 * auto-sized grid row, theme-variable font sizes/colors) which resulted in
 * tab labels not painting at all on the reporter's Android phone. This
 * variant uses only battle-tested primitives:
 *   - TabsList: plain flex row (no grid tracks, no fixed height)
 *   - TabsTrigger: flex-1 equal widths, height from padding (no % calc),
 *     literal px font size and a literal hex base color so the label is
 *     always visible even if attribute selectors or CSS variables fail.
 */

function Tabs({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn("flex flex-col gap-2", className)}
      {...props}
    />
  )
}

function TabsList({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn("flex w-full items-stretch rounded-md p-1", className)}
      {...props}
    />
  )
}

function TabsTrigger({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        "flex flex-1 items-center justify-center rounded-md px-2 py-1.5 text-[12.5px] font-semibold text-[#33455e]",
        "focus-visible:outline-2 focus-visible:outline-[#4a7ebe]",
        "disabled:pointer-events-none disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
}

function TabsContent({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot="tabs-content"
      className={cn("flex-1 outline-none", className)}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTrigger, TabsContent }
