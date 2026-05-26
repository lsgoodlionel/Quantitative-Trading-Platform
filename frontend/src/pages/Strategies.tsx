import { AppShell } from "@/components/layout/AppShell"
import { useStrategies } from "@/hooks/useBacktest"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { useNavigate } from "react-router-dom"

const STRATEGY_LABELS: Record<string, string> = {
  double_ma:          "双均线交叉",
  bollinger:          "布林带",
  macd:               "MACD 信号",
  rsi_mean_reversion: "RSI 均值回归",
  momentum:           "动量策略",
  grid_trading:       "网格交易",
  pairs_trading:      "配对交易",
  multi_factor:       "多因子模型",
}

const STRATEGY_TAGS: Record<string, string[]> = {
  double_ma:          ["趋势跟踪", "技术指标"],
  bollinger:          ["均值回归", "波动率"],
  macd:               ["趋势跟踪", "动量"],
  rsi_mean_reversion: ["均值回归", "超买超卖"],
  momentum:           ["动量", "趋势"],
  grid_trading:       ["网格", "高频"],
  pairs_trading:      ["套利", "配对"],
  multi_factor:       ["多因子", "量化"],
}

export function Strategies() {
  const { data: strategies, isLoading, error } = useStrategies()
  const navigate = useNavigate()

  return (
    <AppShell title="策略管理">
      <div className="mb-6">
        <p className="text-[#8b949e] text-sm">选择预设策略进入回测，支持自定义参数</p>
      </div>

      {isLoading && (
        <div className="flex justify-center py-12">
          <Spinner size="lg" />
        </div>
      )}

      {error && (
        <EmptyState title="加载策略失败" description={error.message} />
      )}

      {strategies && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {strategies.map((s) => (
            <button
              key={s.name}
              onClick={() => navigate(`/backtest?strategy=${s.name}`)}
              className="card text-left hover:border-[#58a6ff]/40 transition-colors group"
            >
              <div className="flex items-start justify-between mb-2">
                <h3 className="text-[#e6edf3] font-semibold text-sm group-hover:text-[#58a6ff] transition-colors">
                  {STRATEGY_LABELS[s.name] ?? s.name}
                </h3>
                <span className="text-[#58a6ff] text-xs opacity-0 group-hover:opacity-100 transition-opacity">
                  回测 →
                </span>
              </div>
              <p className="text-[#8b949e] text-xs leading-relaxed mb-3 line-clamp-2">
                {s.description}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {(STRATEGY_TAGS[s.name] ?? []).map((tag) => (
                  <span
                    key={tag}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-[#1c2128] text-[#8b949e] border border-[#30363d]"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </button>
          ))}
        </div>
      )}
    </AppShell>
  )
}
