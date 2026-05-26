import { clsx } from "clsx"

interface SpinnerProps {
  size?: "sm" | "md" | "lg"
  className?: string
}

const SIZE_MAP = {
  sm: "h-4 w-4 border-2",
  md: "h-6 w-6 border-2",
  lg: "h-10 w-10 border-[3px]",
} as const

export function Spinner({ size = "md", className }: SpinnerProps) {
  return (
    <div
      role="status"
      aria-label="Loading"
      className={clsx(
        "inline-block rounded-full border-[#58a6ff]/30 border-t-[#58a6ff] animate-spin",
        SIZE_MAP[size],
        className,
      )}
    />
  )
}
