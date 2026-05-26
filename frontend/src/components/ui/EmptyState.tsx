interface EmptyStateProps {
  title: string
  description?: string
  action?: React.ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-3 text-[#6e7681] text-5xl select-none">◎</div>
      <p className="text-[#8b949e] font-medium">{title}</p>
      {description && <p className="mt-1 text-sm text-[#6e7681]">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
