import { NavLink } from "react-router-dom"
import { clsx } from "clsx"
import { useAuthStore } from "@/stores/auth"

interface NavItem {
  to: string
  icon: string
  label: string
}

// ── 导航（V3 · H1：16 项收敛到 9 个主页面 + 3 个独立页）────────
// 「价格预警」与「通知中心」已合并为 /alerts 的两个 Tab：规则触发与通知送达
// 本就是一件事的两半，占两格只会让人先猜自己要找的属于哪一半。
// /notifications 仍是有效路由（重定向），书签与历史深链不会 404。
// 按用户工作流顺序：看行情 → 选标的 → 做研究 → 配策略 → 验证 → 组合 → 交易
const NAV_ITEMS: NavItem[] = [
  // ── 核心工作流 ────────────────────────────
  { to: "/",           icon: "🏠", label: "仪表盘" },
  { to: "/market",     icon: "📈", label: "行情"   },
  { to: "/screener",   icon: "🔍", label: "发现"   },
  { to: "/research",   icon: "🔭", label: "研究"   },
  { to: "/strategies", icon: "🔧", label: "策略"   },
  { to: "/backtest",   icon: "🔬", label: "验证"   },
  { to: "/portfolio",  icon: "💼", label: "组合"   },
  { to: "/trading",    icon: "🤖", label: "交易"   },
  // ── 监控 ─────────────────────────────────
  { to: "/risk",          icon: "🛡️", label: "风控"      },
  { to: "/alerts",        icon: "🔔", label: "预警与通知" },
  { to: "/lab",           icon: "📦", label: "投研产物库" },
  // ── 系统 ─────────────────────────────────
  { to: "/settings",   icon: "⚙️", label: "设置"   },
  { to: "/settings/models", icon: "🧠", label: "模型管理" },
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
            // /settings 是 /settings/models 的前缀，不加 end 会两个一起高亮
            end={item.to === "/" || item.to === "/settings"}
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
          <span className="text-base leading-none w-5 text-center shrink-0">🚪</span>
          <span className="hidden lg:block">退出</span>
        </button>
      </div>
    </aside>
  )
}
