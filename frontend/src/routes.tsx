// 应用路由表（V3 · H1）。
//
// 单独成文件的理由：路由表既要被 App 渲染，也要被测试直接检查
// （每条旧路径是否都还在、是否都带 replace 重定向），放在 App.tsx 里就只能整页渲染才能测。
import { Navigate, Route } from "react-router-dom"
import { LegacyRedirect } from "@/components/routing/LegacyRedirect"
import { useAuthStore } from "@/stores/auth"

import { Login } from "@/pages/Login"
import { Dashboard } from "@/pages/Dashboard"
import { MarketPage } from "@/pages/market/MarketPage"
import { Screener } from "@/pages/Screener"
import { ResearchPage } from "@/pages/research/ResearchPage"
import { Strategies } from "@/pages/Strategies"
import { Backtest } from "@/pages/Backtest"
import { PortfolioPage } from "@/pages/portfolio/PortfolioPage"
import { TradingPage } from "@/pages/trading/TradingPage"
import { Settings } from "@/pages/Settings"
import { ModelSettings } from "@/pages/settings/ModelSettings"
import { Risk } from "@/pages/Risk"
import { AlertsPage } from "@/pages/Alerts"
import { Notifications } from "@/pages/Notifications"
import { Lab } from "@/pages/Lab"

export interface LegacyRoute {
  /** 合并前的旧路径 */
  from: string
  /** 合并后的新页面 */
  to: string
  /** 落地时选中的 Tab */
  tab: string
}

/**
 * 页面合并留下的旧路径（V3 · H1）。
 *
 * 用户有书签、有分享出去的链接、浏览器有历史记录 —— 这些路径必须继续可达，
 * 且必须用 replace 跳转（见 LegacyRedirect）。
 */
export const LEGACY_REDIRECTS: LegacyRoute[] = [
  { from: "/market-events",       to: "/market",    tab: "events" },
  { from: "/factor",              to: "/research",  tab: "factor" },
  { from: "/algolab",             to: "/research",  tab: "ml" },
  { from: "/portfolio-optimizer", to: "/portfolio", tab: "optimizer" },
  { from: "/live-strategy",       to: "/trading",   tab: "live" },
  { from: "/orders",              to: "/trading",   tab: "orders" },
]

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />
}

function Protected({ element }: { element: React.ReactNode }) {
  return <ProtectedRoute>{element}</ProtectedRoute>
}

export const appRouteElements = (
  <>
    <Route path="/login" element={<Login />} />

    {/* ── 九个主页面 ── */}
    <Route path="/"           element={<Protected element={<Dashboard />} />} />
    <Route path="/market"     element={<Protected element={<MarketPage />} />} />
    <Route path="/screener"   element={<Protected element={<Screener />} />} />
    <Route path="/research"   element={<Protected element={<ResearchPage />} />} />
    <Route path="/strategies" element={<Protected element={<Strategies />} />} />
    <Route path="/backtest"   element={<Protected element={<Backtest />} />} />
    <Route path="/portfolio"  element={<Protected element={<PortfolioPage />} />} />
    <Route path="/trading"    element={<Protected element={<TradingPage />} />} />
    <Route path="/settings"   element={<Protected element={<Settings />} />} />
    {/* 模型管理独立成页：六张 provider 卡放不进 Settings 单列布局，
        且可深链 —— Copilot 报 501 时能直接把用户丢到这里 */}
    <Route path="/settings/models" element={<Protected element={<ModelSettings />} />} />

    {/* ── 独立页 ── */}
    <Route path="/risk"          element={<Protected element={<Risk />} />} />
    <Route path="/alerts"        element={<Protected element={<AlertsPage />} />} />
    <Route path="/notifications" element={<Protected element={<Notifications />} />} />
    <Route path="/lab"           element={<Protected element={<Lab />} />} />

    {/* ── 合并前的旧路径：重定向，不 404 ── */}
    {LEGACY_REDIRECTS.map(({ from, to, tab }) => (
      <Route key={from} path={from} element={<LegacyRedirect to={to} tab={tab} />} />
    ))}

    <Route path="*" element={<Navigate to="/" replace />} />
  </>
)
