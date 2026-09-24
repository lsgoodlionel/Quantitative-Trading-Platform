// 离散配置：把优化出来的连续权重按最新价格与现金预算换成可执行的整数股数。
import { useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import { usePortfolioAllocate } from "@/hooks/usePortfolio"
import { api } from "@/lib/api"
import type { AllocateResult, AllocationMethod, Bar, Market } from "@/types"
import { ALLOCATION_OPTIONS, type PortfolioOptResult } from "./options"

async function fetchLatestPrices(
  symbols: string[],
  market: Market,
): Promise<{ prices: Record<string, number>; missing: string[] }> {
  const prices: Record<string, number> = {}
  const missing: string[] = []
  await Promise.all(
    symbols.map(async (symbol) => {
      try {
        const qs = new URLSearchParams({ symbol, market, frequency: "1d" })
        const bar = await api.get<Bar | null>(`/api/v1/bars/latest?${qs}`)
        if (bar && bar.close > 0) prices[symbol] = bar.close
        else missing.push(symbol)
      } catch {
        missing.push(symbol)
      }
    }),
  )
  return { prices, missing }
}

function AllocationResultView({ result }: { result: AllocateResult }) {
  const targetSymbols = Object.keys(result.allocation_weights)
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: "配置方法", value: result.method === "lp" ? "整数规划" : "贪心", color: "text-[#58a6ff]" },
          { label: "已配置金额", value: `$${result.allocated_value.toLocaleString()}`, color: "text-[#3fb950]" },
          { label: "剩余现金", value: `$${result.leftover_cash.toLocaleString()}`, color: "text-[#e3b341]" },
          { label: "权重 RMSE", value: result.rmse.toFixed(4), color: "text-[#e6edf3]" },
        ].map(({ label, value, color }) => (
          <div key={label} className="card py-3">
            <p className="text-xs text-[#6e7681] mb-1">{label}</p>
            <p className={`font-mono font-semibold text-sm ${color}`}>{value}</p>
          </div>
        ))}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
              <th className="text-left py-2 pr-3">标的</th>
              <th className="text-right py-2 pr-3">股数</th>
              <th className="text-right py-2 pr-3">实际权重</th>
              <th className="text-right py-2">目标权重</th>
            </tr>
          </thead>
          <tbody>
            {targetSymbols
              .sort((a, b) => (result.allocation_weights[b] ?? 0) - (result.allocation_weights[a] ?? 0))
              .map((sym) => {
                const shares = result.shares[sym] ?? 0
                const realized = (result.allocation_weights[sym] ?? 0) * 100
                return (
                  <tr key={sym} className="border-b border-[#21262d]/50 last:border-0">
                    <td className="py-2 pr-3 font-mono text-[#e6edf3] font-medium">{sym}</td>
                    <td className="py-2 pr-3 text-right font-mono text-[#3fb950]">{shares}</td>
                    <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{realized.toFixed(1)}%</td>
                    <td className="py-2 text-right font-mono text-xs text-[#8b949e]">
                      {realized.toFixed(1)}%
                    </td>
                  </tr>
                )
              })}
          </tbody>
        </table>
      </div>

      {result.skipped.length > 0 && (
        <p className="text-xs text-[#e3b341]">
          ⚠ 以下标的因缺少最新价格被跳过，权重已在其余标的上重新归一化：{result.skipped.join(", ")}
        </p>
      )}
    </div>
  )
}

export function AllocationPanel({
  result,
  market,
}: {
  result: PortfolioOptResult
  market: Market
}) {
  const { mutate: runAllocate, isPending, data: allocation, error, reset } = usePortfolioAllocate()
  const [budget, setBudget] = useState<number>(100000)
  const [method, setMethod] = useState<AllocationMethod>("greedy")
  const [priceError, setPriceError] = useState<string | null>(null)
  const [fetchingPrices, setFetchingPrices] = useState(false)

  async function handleAllocate() {
    setPriceError(null)
    reset()
    if (!(budget > 0)) {
      setPriceError("请输入大于 0 的现金预算")
      return
    }
    const symbols = Object.entries(result.weights)
      .filter(([, w]) => w > 1e-4)
      .map(([sym]) => sym)

    setFetchingPrices(true)
    const { prices, missing } = await fetchLatestPrices(symbols, market)
    setFetchingPrices(false)

    if (Object.keys(prices).length === 0) {
      setPriceError(`未能获取任何标的最新价格${missing.length ? `（${missing.join(", ")}）` : ""}`)
      return
    }

    runAllocate({
      weights: result.weights,
      latest_prices: prices,
      total_value: budget,
      method,
    })
  }

  const busy = fetchingPrices || isPending

  return (
    <div className="card space-y-4 border-[#3fb950]/25">
      <div>
        <h3 className="text-sm font-semibold text-[#e6edf3]">生成整数配股</h3>
        <p className="text-[11px] text-[#6e7681] mt-0.5">
          按最新价格与现金预算，将连续权重转换为可执行的整数股数
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
        <div>
          <label className="label">现金预算</label>
          <input
            type="number"
            min={0}
            step={1000}
            className="input w-full mt-1 font-mono"
            value={budget}
            onChange={(e) => setBudget(Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label">配置算法</label>
          <div className="flex gap-1 mt-1">
            {ALLOCATION_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                title={opt.desc}
                onClick={() => setMethod(opt.value)}
                className={`flex-1 py-1.5 rounded text-xs font-medium border transition-colors ${
                  method === opt.value
                    ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/40"
                    : "text-[#6e7681] border-[#30363d] hover:text-[#e6edf3]"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={handleAllocate}
          className="btn btn-primary w-full"
        >
          {busy ? <Spinner size="sm" className="mx-auto" /> : "生成配股方案"}
        </button>
      </div>

      {fetchingPrices && (
        <p className="text-xs text-[#8b949e]">正在获取最新价格…</p>
      )}
      {priceError && (
        <p className="text-xs text-[#f85149]">{priceError}</p>
      )}
      {error && !busy && (
        <p className="text-xs text-[#f85149]">配置失败：{error.message}</p>
      )}
      {allocation && !busy && <AllocationResultView result={allocation} />}
    </div>
  )
}
