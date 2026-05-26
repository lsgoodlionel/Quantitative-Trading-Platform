import { clsx } from "clsx"

interface PnlCellProps {
  value: number | null | undefined
  suffix?: string
  className?: string
}

export function PnlCell({ value, suffix = "", className }: PnlCellProps) {
  if (value === null || value === undefined) {
    return <span className={clsx("text-[#6e7681] font-mono text-sm", className)}>—</span>
  }
  const isPositive = value >= 0
  return (
    <span
      className={clsx(
        "font-mono text-sm font-medium",
        isPositive ? "text-[#3fb950]" : "text-[#f85149]",
        className,
      )}
    >
      {isPositive ? "+" : ""}
      {value.toFixed(2)}
      {suffix}
    </span>
  )
}

interface PercentCellProps {
  value: number | null | undefined
  className?: string
}

export function PercentCell({ value, className }: PercentCellProps) {
  return <PnlCell value={value} suffix="%" className={className} />
}
