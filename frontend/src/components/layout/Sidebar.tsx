import { NavLink } from "react-router-dom"
import { clsx } from "clsx"
import { useAuthStore } from "@/stores/auth"

interface NavItem {
  to: string
  icon: string
  label: string
}

const NAV_ITEMS: NavItem[] = [
  { to: "/",          icon: "⬡",  label: "仪表盘" },
  { to: "/market",    icon: "📈", label: "行情" },
  { to: "/strategies",icon: "⚙", label: "策略" },
  { to: "/backtest",  icon: "◷",  label: "回测" },
  { to: "/orders",    icon: "≡",  label: "订单" },
  { to: "/portfolio", icon: "◈",  label: "持仓" },
  { to: "/portfolio-optimizer", icon: "⬡", label: "组合优化" },
  { to: "/risk",      icon: "⚑",  label: "风控" },
  { to: "/factor",    icon: "λ",  label: "因子分析" },
  { to: "/algolab",   icon: "∑",  label: "算法实验室" },
  { to: "/settings",  icon: "⚯",  label: "设置" },
]

export function Sidebar() {
  const logout = useAuthStore((s) => s.logout)

  return (
    <aside className="flex flex-col w-16 lg:w-52 h-screen bg-[#161b22] border-r border-[#21262d] shrink-0 fixed left-0 top-0 z-20">
      {/* Logo */}
      <div className="flex items-center gap-2 px-3 py-4 border-b border-[#21262d]">
        <span className="text-[#58a6ff] text-xl font-bold font-mono select-none">Q</span>
        <span className="hidden lg:block text-[#e6edf3] text-sm font-semibold tracking-wide">QuantBot</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 flex flex-col gap-0.5 p-2 overflow-y-auto" aria-label="主导航">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              clsx(
                "flex items-center gap-3 px-2 py-2 rounded-md text-sm transition-colors",
                isActive
                  ? "bg-[#1f6feb]/20 text-[#58a6ff]"
                  : "text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d]",
              )
            }
          >
            <span className="text-base leading-none w-5 text-center shrink-0">{item.icon}</span>
            <span className="hidden lg:block">{item.label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Logout */}
      <div className="p-2 border-t border-[#21262d]">
        <button
          onClick={logout}
          className="flex items-center gap-3 w-full px-2 py-2 rounded-md text-sm text-[#8b949e] hover:text-[#f85149] hover:bg-[#2a1b1b] transition-colors"
          aria-label="退出登录"
        >
          <span className="text-base leading-none w-5 text-center shrink-0">⏻</span>
          <span className="hidden lg:block">退出</span>
        </button>
      </div>
    </aside>
  )
}
