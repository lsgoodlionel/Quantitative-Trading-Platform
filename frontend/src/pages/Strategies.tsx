import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { useStrategies } from "@/hooks/useBacktest"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { useNavigate } from "react-router-dom"

// ── Strategy metadata ──────────────────────────────────────────

type StrategyCategory = "trend" | "mean_reversion" | "arbitrage" | "multi_factor" | "grid"

interface StrategyMeta {
  label: string
  category: StrategyCategory
  tags: string[]
  markets: string[]
  timeframes: string[]
  complexity: "低" | "中" | "高"
  sharpeRange: string   // 历史夏普区间参考
  drawdownRange: string // 历史最大回撤区间参考
  description: string
  params: { name: string; desc: string; default: string }[]
  icon: string
}

const STRATEGY_META: Record<string, StrategyMeta> = {
  double_ma: {
    label: "双均线交叉",
    category: "trend",
    tags: ["趋势跟踪", "技术指标", "经典"],
    markets: ["美股", "港股", "A股"],
    timeframes: ["日线", "周线"],
    complexity: "低",
    sharpeRange: "0.6 ~ 1.2",
    drawdownRange: "8% ~ 20%",
    icon: "📈",
    description: "利用短期均线与长期均线的金叉/死叉信号进行交易。趋势明显时效果最佳，震荡市频繁触发止损。",
    params: [
      { name: "fast_period", desc: "快线周期", default: "10" },
      { name: "slow_period", desc: "慢线周期", default: "30" },
    ],
  },
  bollinger: {
    label: "布林带",
    category: "mean_reversion",
    tags: ["均值回归", "波动率", "通道"],
    markets: ["美股", "港股", "A股"],
    timeframes: ["日线", "1小时"],
    complexity: "低",
    sharpeRange: "0.5 ~ 1.0",
    drawdownRange: "10% ~ 25%",
    icon: "📊",
    description: "当价格触及布林带上轨时做空，触及下轨时做多，等待价格回归均值中轨。震荡市表现更优。",
    params: [
      { name: "period",    desc: "布林带周期", default: "20" },
      { name: "std_dev",   desc: "标准差倍数", default: "2.0" },
    ],
  },
  macd: {
    label: "MACD 信号",
    category: "trend",
    tags: ["趋势跟踪", "动量", "MACD"],
    markets: ["美股", "港股", "A股"],
    timeframes: ["日线", "1小时", "15分钟"],
    complexity: "低",
    sharpeRange: "0.5 ~ 1.1",
    drawdownRange: "12% ~ 28%",
    icon: "📉",
    description: "基于 MACD 快慢线与信号线的交叉进行买卖信号判断，辅以柱状图确认趋势强度。",
    params: [
      { name: "fast",   desc: "快线 EMA", default: "12" },
      { name: "slow",   desc: "慢线 EMA", default: "26" },
      { name: "signal", desc: "信号线",   default: "9"  },
    ],
  },
  rsi_mean_reversion: {
    label: "RSI 均值回归",
    category: "mean_reversion",
    tags: ["均值回归", "超买超卖", "RSI"],
    markets: ["美股", "港股", "A股"],
    timeframes: ["日线", "1小时"],
    complexity: "低",
    sharpeRange: "0.7 ~ 1.3",
    drawdownRange: "8% ~ 18%",
    icon: "🔄",
    description: "RSI 超卖（低于30）买入，RSI 超买（高于70）卖出。适合震荡行情，趋势强烈时需配合趋势过滤。",
    params: [
      { name: "period",    desc: "RSI 周期",    default: "14" },
      { name: "oversold",  desc: "超卖阈值",    default: "30" },
      { name: "overbought",desc: "超买阈值",    default: "70" },
    ],
  },
  momentum: {
    label: "动量策略",
    category: "trend",
    tags: ["动量", "趋势", "截面"],
    markets: ["美股", "港股"],
    timeframes: ["日线", "周线"],
    complexity: "中",
    sharpeRange: "0.8 ~ 1.5",
    drawdownRange: "10% ~ 22%",
    icon: "🚀",
    description: "买入近期表现最强的标的，卖出近期表现最差的标的。基于横截面动量因子，适合多标的组合操作。",
    params: [
      { name: "lookback", desc: "回看周期(天)", default: "60"  },
      { name: "top_n",    desc: "持仓数量",     default: "10"  },
    ],
  },
  grid_trading: {
    label: "网格交易",
    category: "grid",
    tags: ["网格", "震荡市", "中性"],
    markets: ["美股", "A股"],
    timeframes: ["1小时", "15分钟", "5分钟"],
    complexity: "中",
    sharpeRange: "1.0 ~ 2.5",
    drawdownRange: "5% ~ 15%",
    icon: "🔲",
    description: "在预设价格区间内设置等差网格，价格下跌时分批买入，价格上涨时分批卖出，震荡市中稳定收益。",
    params: [
      { name: "grid_num",    desc: "网格数量",     default: "10"   },
      { name: "price_range", desc: "价格区间(%)",  default: "20"   },
      { name: "capital",     desc: "每格资金($)",  default: "1000" },
    ],
  },
  pairs_trading: {
    label: "配对交易",
    category: "arbitrage",
    tags: ["套利", "配对", "统计套利", "中性"],
    markets: ["美股"],
    timeframes: ["日线", "1小时"],
    complexity: "高",
    sharpeRange: "1.2 ~ 2.8",
    drawdownRange: "5% ~ 12%",
    icon: "⚖️",
    description: "寻找协整关系显著的股票对，价差偏离均值时做多低估方、做空高估方，等待价差回归。市场中性策略。",
    params: [
      { name: "z_entry",  desc: "开仓 Z-Score", default: "2.0"  },
      { name: "z_exit",   desc: "平仓 Z-Score", default: "0.5"  },
      { name: "lookback", desc: "协整回看期",    default: "120"  },
    ],
  },
  multi_factor: {
    label: "多因子模型",
    category: "multi_factor",
    tags: ["多因子", "量化", "Alpha", "qlib风格"],
    markets: ["美股", "A股"],
    timeframes: ["日线", "周线"],
    complexity: "高",
    sharpeRange: "1.0 ~ 2.0",
    drawdownRange: "8% ~ 18%",
    icon: "🧠",
    description: "综合价值、质量、动量、低波动等多个因子进行股票打分排名，构建多空或纯多头组合，参考 qlib 因子框架。",
    params: [
      { name: "rebalance_freq", desc: "调仓频率(天)",  default: "20"  },
      { name: "top_pct",        desc: "持仓比例(%)",   default: "20"  },
      { name: "factors",        desc: "启用因子",      default: "全部" },
    ],
  },
}

const CATEGORY_CFG: Record<StrategyCategory, { label: string; color: string; bg: string }> = {
  trend:          { label: "趋势跟踪", color: "#58a6ff", bg: "#1c2a3a" },
  mean_reversion: { label: "均值回归", color: "#3fb950", bg: "#1a2e1a" },
  arbitrage:      { label: "统计套利", color: "#bc8cff", bg: "#271a3a" },
  multi_factor:   { label: "多因子",   color: "#e3b341", bg: "#2e2a1a" },
  grid:           { label: "网格",     color: "#f78166", bg: "#2e1a1a" },
}

const COMPLEXITY_CFG: Record<string, { color: string }> = {
  "低": { color: "#3fb950" },
  "中": { color: "#e3b341" },
  "高": { color: "#f85149" },
}

// ── Filters ────────────────────────────────────────────────────

type CategoryFilter = StrategyCategory | "all"

const FILTER_OPTIONS: { value: CategoryFilter; label: string }[] = [
  { value: "all",            label: "全部" },
  { value: "trend",          label: "趋势" },
  { value: "mean_reversion", label: "均值回归" },
  { value: "arbitrage",      label: "套利" },
  { value: "multi_factor",   label: "多因子" },
  { value: "grid",           label: "网格" },
]

// ── Strategy Card ──────────────────────────────────────────────

interface CardProps {
  name: string
  meta: StrategyMeta
  description: string
  onBacktest: (symbol: string, market: string) => void
}

function StrategyCard({ name, meta, description, onBacktest }: CardProps) {
  const [symbol, setSymbol] = useState("")
  const [market, setMarket] = useState(
    meta.markets[0] === "美股" ? "US" : meta.markets[0] === "港股" ? "HK" : "A"
  )
  const catCfg = CATEGORY_CFG[meta.category]
  const cplxCfg = COMPLEXITY_CFG[meta.complexity]

  const placeholderSymbol =
    market === "US" ? "如 AAPL" : market === "HK" ? "如 00700" : "如 000001"
  const defaultSymbol =
    market === "US" ? "AAPL" : market === "HK" ? "00700" : "000001"

  return (
    <div className="card flex flex-col gap-3 hover:border-[#30363d] transition-colors group">

      {/* Top row: icon + name + category badge */}
      <div className="flex items-start gap-3">
        <span className="text-2xl shrink-0 mt-0.5">{meta.icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-[#e6edf3] font-semibold text-sm leading-tight">{meta.label}</h3>
            <span
              className="text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0"
              style={{ color: catCfg.color, background: catCfg.bg }}
            >
              {catCfg.label}
            </span>
          </div>
          <p className="text-[10px] text-[#6e7681] font-mono mt-0.5">{name}</p>
        </div>
      </div>

      {/* Description */}
      <p className="text-xs text-[#8b949e] leading-relaxed line-clamp-3">
        {description || meta.description}
      </p>

      {/* Tags */}
      <div className="flex flex-wrap gap-1.5">
        {meta.tags.map((tag) => (
          <span
            key={tag}
            className="text-[10px] px-1.5 py-0.5 rounded bg-[#1c2128] text-[#8b949e] border border-[#30363d]"
          >
            {tag}
          </span>
        ))}
      </div>

      {/* Metrics row */}
      <div className="grid grid-cols-3 gap-2 py-2 border-t border-b border-[#21262d]">
        <div>
          <p className="text-[10px] text-[#6e7681] mb-0.5">复杂度</p>
          <p className="text-xs font-semibold" style={{ color: cplxCfg.color }}>{meta.complexity}</p>
        </div>
        <div>
          <p className="text-[10px] text-[#6e7681] mb-0.5">夏普参考</p>
          <p className="text-xs font-mono text-[#e6edf3]">{meta.sharpeRange}</p>
        </div>
        <div>
          <p className="text-[10px] text-[#6e7681] mb-0.5">最大回撤</p>
          <p className="text-xs font-mono text-[#f85149]">{meta.drawdownRange}</p>
        </div>
      </div>

      {/* Markets + timeframes */}
      <div className="flex gap-4 text-[10px]">
        <div>
          <span className="text-[#6e7681] mr-1">市场:</span>
          <span className="text-[#8b949e]">{meta.markets.join(" · ")}</span>
        </div>
        <div>
          <span className="text-[#6e7681] mr-1">周期:</span>
          <span className="text-[#8b949e]">{meta.timeframes.join(" · ")}</span>
        </div>
      </div>

      {/* Params preview */}
      {meta.params.length > 0 && (
        <div className="bg-[#0d1117] rounded-md px-3 py-2">
          <p className="text-[10px] text-[#6e7681] mb-1.5 font-medium">关键参数</p>
          <div className="flex flex-col gap-1">
            {meta.params.slice(0, 3).map((p) => (
              <div key={p.name} className="flex justify-between text-[10px]">
                <span className="text-[#8b949e]">{p.desc}</span>
                <span className="font-mono text-[#58a6ff]">{p.default}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 市场 + 标的快捷输入 */}
      <div className="flex gap-1.5">
        {/* 市场选择 */}
        {meta.markets.length > 1 && (
          <div className="flex rounded border border-[#30363d] overflow-hidden shrink-0">
            {meta.markets.map((m) => {
              const code = m === "美股" ? "US" : m === "港股" ? "HK" : "A"
              return (
                <button key={m} type="button"
                  onClick={() => { setMarket(code); setSymbol("") }}
                  className={`px-2 py-1 text-[10px] font-medium transition-colors ${
                    market === code
                      ? "bg-[#1f6feb]/20 text-[#58a6ff]"
                      : "text-[#6e7681] hover:text-[#e6edf3] hover:bg-[#21262d]"
                  }`}>{m === "美股" ? "🇺🇸" : m === "港股" ? "🇭🇰" : "🇨🇳"}{m.replace("股", "")}</button>
              )
            })}
          </div>
        )}
        {/* 标的代码 */}
        <input
          type="text"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder={placeholderSymbol}
          className="input flex-1 text-xs font-mono py-1.5 min-w-0"
        />
      </div>

      {/* Action button */}
      <button
        onClick={() => onBacktest(symbol.trim() || defaultSymbol, market)}
        className="w-full py-2 rounded-md text-xs font-medium bg-[#1c2128] text-[#58a6ff]
                   border border-[#30363d] hover:bg-[#21262d] hover:border-[#58a6ff]/40
                   transition-all group-hover:text-[#79c0ff]"
      >
        ▶ 开始回测 {symbol ? symbol : defaultSymbol}
      </button>
    </div>
  )
}

// ── Main Strategies Page ───────────────────────────────────────

export function Strategies() {
  const { data: strategies, isLoading, error } = useStrategies()
  const navigate = useNavigate()
  const [filter, setFilter] = useState<CategoryFilter>("all")
  const [search, setSearch] = useState("")

  const filtered = (strategies ?? []).filter((s) => {
    const meta = STRATEGY_META[s.name]
    if (!meta) return true
    const matchCategory = filter === "all" || meta.category === filter
    const q = search.trim().toLowerCase()
    const matchSearch = !q
      || s.name.includes(q)
      || meta.label.toLowerCase().includes(q)
      || meta.tags.some((t) => t.toLowerCase().includes(q))
    return matchCategory && matchSearch
  })

  return (
    <AppShell title="策略管理" help={PAGE_HELP["strategies"]}>

      {/* Header */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <div className="flex-1">
          <p className="text-[#8b949e] text-sm mb-3">
            选择预设策略进入回测，参数均可自定义。策略评分基于历史模拟，仅供参考。
          </p>
          {/* Category filter */}
          <div className="flex gap-1 bg-[#161b22] rounded-lg p-1 w-fit border border-[#21262d]">
            {FILTER_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setFilter(opt.value)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  filter === opt.value
                    ? "bg-[#21262d] text-[#e6edf3] shadow"
                    : "text-[#8b949e] hover:text-[#e6edf3]"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Search */}
        <div className="sm:w-56">
          <input
            type="text"
            placeholder="搜索策略..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-[#0d1117] border border-[#30363d] rounded-lg px-3 py-2 text-sm
                       text-[#e6edf3] placeholder-[#6e7681] focus:outline-none
                       focus:border-[#58a6ff]/50 transition-colors"
          />
        </div>
      </div>

      {/* Stats bar */}
      {strategies && (
        <div className="flex gap-4 mb-5 text-xs text-[#6e7681]">
          <span>共 <span className="text-[#e6edf3] font-mono">{strategies.length}</span> 个策略</span>
          <span>显示 <span className="text-[#e6edf3] font-mono">{filtered.length}</span> 个</span>
          {Object.entries(CATEGORY_CFG).map(([key, cfg]) => {
            const count = (strategies ?? []).filter(
              (s) => STRATEGY_META[s.name]?.category === key
            ).length
            return count > 0 ? (
              <span key={key} style={{ color: cfg.color }}>
                {cfg.label} {count}
              </span>
            ) : null
          })}
        </div>
      )}

      {/* Loading / error */}
      {isLoading && (
        <div className="flex justify-center py-12"><Spinner size="lg" /></div>
      )}
      {error && (
        <EmptyState title="加载策略失败" description={error.message} />
      )}

      {/* Grid */}
      {filtered.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filtered.map((s) => {
            const meta = STRATEGY_META[s.name]
            if (!meta) return null
            return (
              <StrategyCard
                key={s.name}
                name={s.name}
                meta={meta}
                description={s.description}
                onBacktest={(sym, mkt) => navigate(`/backtest?strategy=${s.name}&symbol=${sym}&market=${mkt}`)}
              />
            )
          })}
        </div>
      )}

      {!isLoading && filtered.length === 0 && strategies && strategies.length > 0 && (
        <EmptyState
          title="没有匹配的策略"
          description="尝试调整筛选条件或搜索关键词"
        />
      )}

    </AppShell>
  )
}
