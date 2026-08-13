import type { Market, Frequency } from "@/types"
import type { FactorInfo } from "@/hooks/useFactorAnalysis"

export const MARKETS: Market[] = ["US", "HK", "A"]
export const FREQS: Frequency[] = ["1d", "1w"]
export const FORMULA_SENTINEL = "__formula__"

/** 解析逗号/空白分隔的标的输入为去重大写数组 */
export function parseUniverse(raw: string): string[] {
  return Array.from(
    new Set(
      raw
        .split(/[\s,，、]+/)
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean),
    ),
  )
}

interface UniverseHeaderProps {
  universe: string
  onUniverse: (v: string) => void
  market: Market
  onMarket: (m: Market) => void
  freq: Frequency
  onFreq: (f: Frequency) => void
  baseFactor: string
  onBaseFactor: (name: string) => void
  factorList: FactorInfo[]
  allowFormula?: boolean
}

/** 共享的「标的 + 市场 + 频率 + 基础因子」配置头，供 B1/B4 两个页签复用 */
export function UniverseHeader({
  universe, onUniverse, market, onMarket, freq, onFreq,
  baseFactor, onBaseFactor, factorList, allowFormula = true,
}: UniverseHeaderProps) {
  const count = parseUniverse(universe).length
  return (
    <div className="card space-y-3">
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="label">标的池（Universe）</label>
          <span className="text-[10px] text-[#6e7681]">{count} 只 · 需 ≥ 2</span>
        </div>
        <textarea
          className="input w-full font-mono text-xs h-16 resize-none uppercase"
          value={universe}
          onChange={(e) => onUniverse(e.target.value)}
          placeholder="AAPL, MSFT, GOOGL, AMZN, NVDA"
        />
        <p className="text-[10px] text-[#6e7681] mt-1">逗号或空格分隔，横截面处理在同一时刻跨标的进行</p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label block mb-1">市场</label>
          <div className="flex gap-1">
            {MARKETS.map((m) => (
              <button key={m} onClick={() => onMarket(m)}
                className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
                  market === m
                    ? "bg-[#1f6feb]/30 text-[#58a6ff] border border-[#58a6ff]/30"
                    : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
                }`}>{m}</button>
            ))}
          </div>
        </div>
        <div>
          <label className="label block mb-1">频率</label>
          <div className="flex gap-1">
            {FREQS.map((f) => (
              <button key={f} onClick={() => onFreq(f)}
                className={`flex-1 py-1.5 rounded text-xs font-medium transition-colors ${
                  freq === f
                    ? "bg-[#1f6feb]/30 text-[#58a6ff] border border-[#58a6ff]/30"
                    : "text-[#8b949e] border border-[#30363d] hover:text-[#e6edf3]"
                }`}>{f}</button>
            ))}
          </div>
        </div>
      </div>

      <div>
        <label className="label block mb-1">基础因子</label>
        <select
          className="input w-full text-xs"
          value={baseFactor}
          onChange={(e) => onBaseFactor(e.target.value)}
        >
          {factorList.map((f) => (
            <option key={f.name} value={f.name}>{f.label}（{f.group}）</option>
          ))}
          {allowFormula && <option value={FORMULA_SENTINEL}>⚡ 自定义公式（在「公式因子」页签构建）</option>}
        </select>
      </div>
    </div>
  )
}

/** 数值格式化 */
export function fmt(v: number | null | undefined, d = 4): string {
  if (v == null || isNaN(v)) return "—"
  return v.toFixed(d)
}
