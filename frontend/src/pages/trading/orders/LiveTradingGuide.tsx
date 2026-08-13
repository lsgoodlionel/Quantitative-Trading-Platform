// 实盘接入引导：按券商配置状态给出「已实盘 / 已模拟 / 未配置」三种提示。
import { Link } from "react-router-dom"

export function LiveTradingGuide({ tradingMode }: { tradingMode?: { configured: boolean; paper_mode: boolean; mode_label: string } }) {
  if (!tradingMode) return null

  // 已配置实盘
  if (tradingMode.configured && !tradingMode.paper_mode) {
    return (
      <div className="mb-5 rounded-xl border border-[#f85149]/40 bg-[#2a1515] p-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="w-2 h-2 rounded-full bg-[#f85149] animate-pulse" />
          <span className="text-sm font-bold text-[#f85149]">💰 实盘模式 — 真实资金</span>
          <span className="text-[10px] text-[#f85149]/60 ml-auto">Alpaca Live Trading</span>
        </div>
        <p className="text-xs text-[#f85149]/80 leading-relaxed">
          当前连接的是 <strong>真实账户</strong>，下单将使用真实资金。请仔细确认每笔订单的标的、数量和价格。
          如需切换模拟盘，请前往
          <Link to="/settings" className="text-[#f85149] underline ml-1">设置 → 美股通道</Link>
          将模式改为 Paper Trading。
        </p>
      </div>
    )
  }

  // 已配置模拟盘
  if (tradingMode.configured && tradingMode.paper_mode) {
    return (
      <div className="mb-5 rounded-xl border border-[#58a6ff]/25 bg-[#111d2e] p-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="w-2 h-2 rounded-full bg-[#58a6ff]" />
          <span className="text-sm font-semibold text-[#58a6ff]">📋 模拟盘模式 — Alpaca Paper Trading</span>
        </div>
        <p className="text-xs text-[#8b949e] leading-relaxed">
          订单通过 Alpaca Paper Trading 接口执行，<strong className="text-[#e6edf3]">不使用真实资金</strong>。
          策略调试完成后，可在
          <Link to="/settings" className="text-[#58a6ff] underline mx-1">设置 → 美股通道</Link>
          切换为实盘账户。
        </p>
        <div className="mt-3 flex gap-2 flex-wrap">
          <Link to="/trading?tab=live"
            className="px-3 py-1.5 rounded text-xs border border-[#3fb950]/30 text-[#3fb950] hover:bg-[#3fb950]/10 transition-colors">
            ▶ 查看策略模拟盘
          </Link>
          <Link to="/portfolio"
            className="px-3 py-1.5 rounded text-xs border border-[#58a6ff]/30 text-[#58a6ff] hover:bg-[#58a6ff]/10 transition-colors">
            💼 查看持仓
          </Link>
        </div>
      </div>
    )
  }

  // 未配置 Alpaca
  return (
    <div className="mb-5 rounded-xl border border-[#e3b341]/30 bg-[#1a1400] p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-lg">⚙️</span>
        <span className="text-sm font-semibold text-[#e3b341]">尚未配置券商接口 — 如何开始实盘？</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
        {[
          { step: "1", title: "配置 Alpaca 密钥", desc: "前往「设置」→「美股通道」，填入 Alpaca API Key。注册免费：alpaca.markets", href: "/settings", label: "去配置 →" },
          { step: "2", title: "选择交易模式", desc: "Paper Trading 使用模拟账户（免费，推荐先用），Live Trading 使用真实资金。", href: null, label: "" },
          { step: "3", title: "通过策略或手动下单", desc: "使用「模拟/实盘」页运行量化策略，或在本页「快速下单」面板手动下单。", href: "/trading?tab=live", label: "启动策略 →" },
        ].map((s) => (
          <div key={s.step} className="bg-[#0d1117] rounded-lg p-3">
            <div className="flex items-center gap-2 mb-1.5">
              <span className="w-5 h-5 rounded-full bg-[#e3b341]/20 text-[#e3b341] text-[10px] font-bold flex items-center justify-center">{s.step}</span>
              <span className="font-medium text-[#e6edf3]">{s.title}</span>
            </div>
            <p className="text-[#6e7681] leading-relaxed mb-2">{s.desc}</p>
            {s.href && (
              <Link to={s.href}
                className="inline-block text-[10px] px-2 py-1 rounded border border-[#e3b341]/30 text-[#e3b341] hover:bg-[#e3b341]/10 transition-colors">
                {s.label}
              </Link>
            )}
          </div>
        ))}
      </div>
      <p className="text-[10px] text-[#6e7681]">
        ⚠ 未配置 Alpaca 时，本页订单仅在本地记录，不会连接真实或模拟账户。
      </p>
    </div>
  )
}
