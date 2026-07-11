import { useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { Modal } from "@/components/ui/Modal"
import { useToast } from "@/components/ui/Toast"
import {
  usePairlistRun,
  useSavedPairlists,
  useSavePairlist,
  useDeletePairlist,
  RULE_META,
  RULE_KINDS,
  type PairlistRule,
  type PairlistRuleKind,
  type PairMetrics,
  type SavedPairlist,
} from "@/hooks/useDynamicPairlist"
import type { Market } from "@/types"

// ── 格式化 ──────────────────────────────────────────────────────
function fmt(v: number | null, d = 2): string {
  return v == null ? "—" : v.toFixed(d)
}
function fmtVol(v: number | null): string {
  if (v == null) return "—"
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(1)}万`
  return `${v}`
}
function perfColor(v: number | null): string {
  if (v == null) return "text-[#8b949e]"
  return v > 0 ? "text-[#3fb950]" : v < 0 ? "text-[#f85149]" : "text-[#e6edf3]"
}

const INPUT_CLS =
  "w-full bg-[#0d1117] border border-[#30363d] rounded px-2 py-1.5 text-sm text-[#e6edf3] " +
  "focus:border-[#58a6ff] focus:outline-none placeholder:text-[#484f58]"

const DEFAULT_RULE: PairlistRule = { kind: "volume", min_value: null, max_value: null, sort: null, top: null }

// ── 单条规则编辑行 ──────────────────────────────────────────────
function RuleRow({
  rule,
  index,
  onChange,
  onRemove,
  onMove,
  isFirst,
  isLast,
}: {
  rule: PairlistRule
  index: number
  onChange: (patch: Partial<PairlistRule>) => void
  onRemove: () => void
  onMove: (dir: -1 | 1) => void
  isFirst: boolean
  isLast: boolean
}) {
  const meta = RULE_META[rule.kind]
  const parse = (s: string): number | null => (s === "" ? null : Number(s))
  return (
    <div className="grid grid-cols-1 md:grid-cols-[auto_140px_1fr_1fr_110px_90px_auto] gap-2 items-center bg-[#0d1117] border border-[#21262d] rounded-lg p-2">
      <span className="text-xs text-[#484f58] w-6 text-center">{index + 1}</span>
      <select
        className={INPUT_CLS}
        value={rule.kind}
        onChange={(e) => onChange({ kind: e.target.value as PairlistRuleKind })}
      >
        {RULE_KINDS.map((k) => (
          <option key={k} value={k}>{RULE_META[k].label}</option>
        ))}
      </select>
      <input
        type="number" step={meta.step} placeholder={`最小 (${meta.unit})`} className={INPUT_CLS}
        value={rule.min_value ?? ""} onChange={(e) => onChange({ min_value: parse(e.target.value) })}
      />
      <input
        type="number" step={meta.step} placeholder={`最大 (${meta.unit})`} className={INPUT_CLS}
        value={rule.max_value ?? ""} onChange={(e) => onChange({ max_value: parse(e.target.value) })}
      />
      <select
        className={INPUT_CLS} value={rule.sort ?? ""}
        onChange={(e) => onChange({ sort: (e.target.value || null) as PairlistRule["sort"] })}
        title="按该维度排序"
      >
        <option value="">不排序</option>
        <option value="desc">降序 ↓</option>
        <option value="asc">升序 ↑</option>
      </select>
      <input
        type="number" step={1} placeholder="TopN" className={INPUT_CLS}
        value={rule.top ?? ""} onChange={(e) => onChange({ top: parse(e.target.value) })}
        title="保留头部 N 个"
      />
      <div className="flex items-center gap-1 justify-end">
        <button onClick={() => onMove(-1)} disabled={isFirst}
          className="px-1.5 text-[#8b949e] hover:text-[#e6edf3] disabled:opacity-30" title="上移">↑</button>
        <button onClick={() => onMove(1)} disabled={isLast}
          className="px-1.5 text-[#8b949e] hover:text-[#e6edf3] disabled:opacity-30" title="下移">↓</button>
        <button onClick={onRemove}
          className="px-1.5 text-[#f85149] hover:text-[#ff7b72]" title="删除">✕</button>
      </div>
    </div>
  )
}

// ── 结果表 ──────────────────────────────────────────────────────
function ResultTable({ rows, onGo }: { rows: PairMetrics[]; onGo: (c: PairMetrics) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm min-w-[820px]">
        <thead>
          <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
            <th className="text-left py-2 px-3">代码 / 名称</th>
            <th className="text-right py-2 px-2">现价</th>
            <th className="text-right py-2 px-2">成交量</th>
            <th className="text-right py-2 px-2">市值(亿)</th>
            <th className="text-right py-2 px-2">波动率%</th>
            <th className="text-right py-2 px-2">近期表现%</th>
            <th className="text-right py-2 px-2">价差%</th>
            <th className="text-right py-2 px-3">操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={`${c.market}-${c.symbol}`} className="border-b border-[#21262d]/40 hover:bg-[#161b22]">
              <td className="py-2 px-3">
                <span className="font-mono text-[#58a6ff]">{c.symbol}</span>
                <span className="text-[#e6edf3] ml-2">{c.name}</span>
              </td>
              <td className="py-2 px-2 text-right font-mono text-[#e6edf3]">{fmt(c.price)}</td>
              <td className="py-2 px-2 text-right font-mono text-[#8b949e]">{fmtVol(c.volume)}</td>
              <td className="py-2 px-2 text-right font-mono text-[#e6edf3]">{fmt(c.market_cap_yi, 1)}</td>
              <td className="py-2 px-2 text-right font-mono text-[#e3b341]">{fmt(c.volatility, 1)}</td>
              <td className={`py-2 px-2 text-right font-mono ${perfColor(c.performance)}`}>{fmt(c.performance, 1)}</td>
              <td className="py-2 px-2 text-right font-mono text-[#8b949e]">{fmt(c.spread_proxy, 2)}</td>
              <td className="py-2 px-3 text-right whitespace-nowrap">
                <button onClick={() => onGo(c)} className="text-xs text-[#58a6ff] hover:underline">行情</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── 主面板 ──────────────────────────────────────────────────────
export function DynamicPairlist({ market }: { market: Market }) {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [rules, setRules] = useState<PairlistRule[]>([{ ...DEFAULT_RULE, min_value: 100000, sort: "desc", top: 30 }])
  const [lookback, setLookback] = useState(20)
  const [saveOpen, setSaveOpen] = useState(false)
  const [saveName, setSaveName] = useState("")
  const [editingId, setEditingId] = useState<string | null>(null)

  const runM = usePairlistRun()
  const savedQ = useSavedPairlists()
  const saveM = useSavePairlist()
  const delM = useDeletePairlist()

  const result = runM.data

  const patchRule = (i: number, patch: Partial<PairlistRule>) =>
    setRules((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  const addRule = () => setRules((prev) => [...prev, { ...DEFAULT_RULE }])
  const removeRule = (i: number) => setRules((prev) => prev.filter((_, idx) => idx !== i))
  const moveRule = (i: number, dir: -1 | 1) =>
    setRules((prev) => {
      const j = i + dir
      if (j < 0 || j >= prev.length) return prev
      const next = [...prev]
      ;[next[i], next[j]] = [next[j], next[i]]
      return next
    })

  const run = () => {
    runM.mutate(
      { market, rules, lookback_days: lookback },
      { onError: (e) => toast(`运行失败: ${e.message}`, "error") },
    )
  }

  const openSave = () => {
    setEditingId(null)
    setSaveName("")
    setSaveOpen(true)
  }

  const doSave = () => {
    if (!saveName.trim()) {
      toast("请输入标的池名称", "error")
      return
    }
    saveM.mutate(
      { id: editingId, name: saveName.trim(), market, rules, lookback_days: lookback },
      {
        onSuccess: () => {
          toast("标的池已保存", "success")
          setSaveOpen(false)
        },
        onError: (e) => toast(`保存失败: ${e.message}`, "error"),
      },
    )
  }

  const loadSaved = (p: SavedPairlist) => {
    setRules(p.rules.length ? p.rules : [{ ...DEFAULT_RULE }])
    setLookback(p.lookback_days)
    setEditingId(p.id)
    setSaveName(p.name)
    toast(`已载入「${p.name}」，可运行或另存`, "info")
  }

  const removeSaved = (p: SavedPairlist) => {
    delM.mutate(p.id, {
      onSuccess: () => toast("已删除", "success"),
      onError: (e) => toast(`删除失败: ${e.message}`, "error"),
    })
  }

  const goMarket = (c: PairMetrics) =>
    navigate(`/market?symbol=${encodeURIComponent(c.symbol)}&market=${c.market}`)

  const saved = savedQ.data ?? []
  const hasBarsRule = useMemo(
    () => rules.some((r) => ["volatility", "performance", "spread"].includes(r.kind)),
    [rules],
  )

  return (
    <div className="flex flex-col gap-5">
      {/* 已保存标的池 */}
      {saved.length > 0 && (
        <div>
          <p className="text-xs text-[#8b949e] mb-2">已保存标的池（点击载入规则链）</p>
          <div className="flex flex-wrap gap-2">
            {saved.map((p) => (
              <div key={p.id}
                className="flex items-center gap-1 rounded-lg border border-[#30363d] bg-[#161b22] pl-3 pr-1.5 py-1">
                <button onClick={() => loadSaved(p)}
                  className="text-sm text-[#e6edf3] hover:text-[#58a6ff]" title={`${p.market} · ${p.rules.length} 条规则`}>
                  {p.name}
                </button>
                <span className="text-xs text-[#484f58]">{p.market}</span>
                <button onClick={() => removeSaved(p)}
                  className="text-xs text-[#8b949e] hover:text-[#f85149] px-1" title="删除">✕</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 规则链编辑 */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-[#e6edf3]">过滤规则链（自上而下顺序执行）</h3>
          <div className="flex items-center gap-2">
            <label className="text-xs text-[#8b949e]">回看天数</label>
            <input type="number" min={2} max={120} value={lookback}
              onChange={(e) => setLookback(Math.max(2, Math.min(120, Number(e.target.value) || 20)))}
              className="w-16 bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-sm text-[#e6edf3] focus:border-[#58a6ff] focus:outline-none"
              title="波动率/近期表现/价差 的历史回看窗口" />
          </div>
        </div>

        <div className="flex flex-col gap-2">
          {rules.map((r, i) => (
            <RuleRow key={i} rule={r} index={i} isFirst={i === 0} isLast={i === rules.length - 1}
              onChange={(patch) => patchRule(i, patch)}
              onRemove={() => removeRule(i)}
              onMove={(dir) => moveRule(i, dir)} />
          ))}
          {rules.length === 0 && (
            <p className="text-sm text-[#8b949e] py-3 text-center">尚无规则，点击下方添加</p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3 mt-4">
          <button onClick={addRule}
            className="px-3 py-1.5 rounded-lg border border-[#30363d] text-sm text-[#8b949e] hover:text-[#e6edf3] hover:border-[#58a6ff] transition-colors">
            + 添加规则
          </button>
          <button onClick={run} disabled={runM.isPending || rules.length === 0}
            className="px-5 py-1.5 rounded-lg bg-[#238636] text-white text-sm font-medium hover:bg-[#2ea043] disabled:opacity-50 transition-colors">
            {runM.isPending ? "运行中…" : "运行规则链"}
          </button>
          <button onClick={openSave} disabled={rules.length === 0}
            className="px-4 py-1.5 rounded-lg border border-[#30363d] text-sm text-[#58a6ff] hover:border-[#58a6ff] disabled:opacity-50 transition-colors">
            保存标的池
          </button>
          {result && (
            <span className="text-xs text-[#8b949e]">
              产出 {result.count} / {result.universe_size} 只（回看 {result.lookback_days} 日）
            </span>
          )}
          {hasBarsRule && (
            <span className="text-xs text-[#484f58]">含波动/表现/价差规则，首次运行需拉历史日线，稍慢</span>
          )}
        </div>
      </div>

      {/* 结果 */}
      <div className="bg-[#161b22] border border-[#21262d] rounded-lg p-4">
        {runM.isPending ? (
          <div className="py-16 flex justify-center"><Spinner /></div>
        ) : !result ? (
          <EmptyState title="配置规则链后运行" description="按 成交量/波动/价格/价差/市值/近期表现 链式过滤，构建可交易标的池" />
        ) : result.items.length === 0 ? (
          <EmptyState title="无标的通过全部规则" description="放宽阈值或减少规则后重试（部分标的历史/基本面数据可能缺失）" />
        ) : (
          <ResultTable rows={result.items} onGo={goMarket} />
        )}
      </div>

      {/* 保存弹窗 */}
      <Modal open={saveOpen} onClose={() => setSaveOpen(false)} title={editingId ? "更新标的池" : "保存标的池"}>
        <div className="flex flex-col gap-4">
          <div>
            <label className="block text-xs text-[#8b949e] mb-1">名称</label>
            <input value={saveName} onChange={(e) => setSaveName(e.target.value)}
              placeholder="如：港股高流动低波动" className={INPUT_CLS} maxLength={60} autoFocus />
          </div>
          <p className="text-xs text-[#8b949e]">
            市场 {market} · {rules.length} 条规则 · 回看 {lookback} 日
            {editingId && "（将覆盖同名已存标的池）"}
          </p>
          <div className="flex justify-end gap-2">
            <button onClick={() => setSaveOpen(false)}
              className="px-4 py-1.5 rounded-lg border border-[#30363d] text-sm text-[#8b949e] hover:text-[#e6edf3]">
              取消
            </button>
            <button onClick={doSave} disabled={saveM.isPending}
              className="px-4 py-1.5 rounded-lg bg-[#238636] text-white text-sm font-medium hover:bg-[#2ea043] disabled:opacity-50">
              {saveM.isPending ? "保存中…" : "保存"}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
