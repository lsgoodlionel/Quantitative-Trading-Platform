// Black-Litterman 观点编辑器：绝对/相对观点 + Idzorek 置信度滑杆。
import type { BLViewInput } from "@/hooks/usePortfolioAdvanced"

const EMPTY_VIEW: BLViewInput = { kind: "absolute", assets: [""], value: 0.1, confidence: 0.5 }

function SymbolSelect({
  value,
  symbols,
  onChange,
}: {
  value: string
  symbols: string[]
  onChange: (s: string) => void
}) {
  return (
    <select className="input flex-1 text-xs font-mono py-1"
      value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">选择标的</option>
      {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
    </select>
  )
}

export function BLViewsEditor({
  views,
  symbols,
  onChange,
}: {
  views: BLViewInput[]
  symbols: string[]
  onChange: (views: BLViewInput[]) => void
}) {
  function updateView(idx: number, patch: Partial<BLViewInput>) {
    onChange(views.map((v, i) => (i === idx ? { ...v, ...patch } : v)))
  }
  function setKind(idx: number, kind: BLViewInput["kind"]) {
    const v = views[idx]
    const assets = kind === "relative"
      ? [v.assets[0] ?? "", v.assets[1] ?? ""]
      : [v.assets[0] ?? ""]
    updateView(idx, { kind, assets })
  }
  function setAsset(idx: number, pos: number, sym: string) {
    const assets = [...views[idx].assets]
    assets[pos] = sym
    updateView(idx, { assets })
  }
  function addView() {
    onChange([...views, { ...EMPTY_VIEW, assets: [symbols[0] ?? ""] }])
  }
  function removeView(idx: number) {
    onChange(views.filter((_, i) => i !== idx))
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="label">投资者观点（Black-Litterman）</label>
        <button type="button" onClick={addView}
          className="text-[11px] px-2 py-0.5 rounded border border-[#58a6ff]/40 text-[#58a6ff] hover:bg-[#1f6feb]/10">
          + 添加观点
        </button>
      </div>
      {views.length === 0 && (
        <p className="text-[10px] text-[#e3b341]">至少添加 1 条观点后才能运行 BL 优化</p>
      )}
      <div className="space-y-2">
        {views.map((v, idx) => (
          <div key={idx} className="border border-[#30363d] rounded p-2 space-y-2 bg-[#0d1117]">
            <div className="flex items-center gap-1">
              {(["absolute", "relative"] as const).map((k) => (
                <button key={k} type="button" onClick={() => setKind(idx, k)}
                  className={`flex-1 py-1 rounded text-[10px] border transition-colors ${
                    v.kind === k
                      ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/40"
                      : "text-[#6e7681] border-[#30363d] hover:text-[#e6edf3]"
                  }`}>
                  {k === "absolute" ? "绝对（收益=）" : "相对（跑赢）"}
                </button>
              ))}
              <button type="button" onClick={() => removeView(idx)}
                title="删除观点"
                className="px-1.5 py-1 rounded text-[10px] text-[#f85149] border border-[#f85149]/30 hover:bg-[#f85149]/10">
                ✕
              </button>
            </div>
            <div className="flex items-center gap-1.5 text-xs">
              <SymbolSelect value={v.assets[0] ?? ""} symbols={symbols}
                onChange={(s) => setAsset(idx, 0, s)} />
              {v.kind === "relative" && (
                <>
                  <span className="text-[#6e7681] text-[10px] shrink-0">跑赢</span>
                  <SymbolSelect value={v.assets[1] ?? ""} symbols={symbols}
                    onChange={(s) => setAsset(idx, 1, s)} />
                </>
              )}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[10px] text-[#6e7681]">
                  {v.kind === "absolute" ? "年化收益 %" : "超额收益 %"}
                </label>
                <input type="number" step={1} className="input w-full mt-0.5 font-mono text-xs"
                  value={Math.round(v.value * 1000) / 10}
                  onChange={(e) => updateView(idx, { value: Number(e.target.value) / 100 })} />
              </div>
              <div>
                <label className="text-[10px] text-[#6e7681]">置信度 {(v.confidence * 100).toFixed(0)}%</label>
                <input type="range" min={0} max={1} step={0.05} className="w-full mt-2 accent-[#58a6ff]"
                  value={v.confidence}
                  onChange={(e) => updateView(idx, { confidence: Number(e.target.value) })} />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
