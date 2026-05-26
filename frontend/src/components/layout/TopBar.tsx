import { useAuthStore } from "@/stores/auth"

interface TopBarProps {
  title: string
}

export function TopBar({ title }: TopBarProps) {
  const user = useAuthStore((s) => s.user)

  return (
    <header className="h-12 flex items-center justify-between px-4 lg:px-6 border-b border-[#21262d] bg-[#0d1117] shrink-0">
      <h1 className="text-[#e6edf3] text-sm font-semibold">{title}</h1>
      <div className="flex items-center gap-3 text-xs text-[#8b949e]">
        <span
          className="inline-flex items-center gap-1.5"
          title="API 连接状态"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-[#3fb950] animate-pulse" />
          <span className="hidden sm:inline">已连接</span>
        </span>
        {user && (
          <span className="font-mono text-[#6e7681]">{user}</span>
        )}
      </div>
    </header>
  )
}
