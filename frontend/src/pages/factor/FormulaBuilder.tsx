import { useMemo } from "react"
import { Spinner } from "@/components/ui/Spinner"
import type {
  FormulaMeta, FormulaFeature, FormulaOperator, FormulaPreset,
} from "@/hooks/useFactorAnalysis"

// ── RPN 栈校验 ──────────────────────────────────────────────────
// 返回 { valid, depth, error }，用于实时提示公式是否平衡

interface StackState {
  valid: boolean
  depth: number
  error: string | null
}

function validateTokens(
  tokens: string[],
  featureNames: Set<string>,
  opArity: Map<string, number>,
): StackState {
  let depth = 0
  for (const tok of tokens) {
    if (featureNames.has(tok)) {
      depth += 1
    } else if (opArity.has(tok)) {
      const arity = opArity.get(tok)!
      if (depth < arity) {
        return { valid: false, depth, error: `算子 ${tok} 需 ${arity} 个操作数，当前栈深 ${depth}` }
      }
      depth = depth - arity + 1
    } else {
      return { valid: false, depth, error: `未知 token: ${tok}` }
    }
  }
  if (tokens.length === 0) return { valid: false, depth: 0, error: null }
  if (depth !== 1) return { valid: false, depth, error: `公式不平衡：栈深 ${depth}（应为 1）` }
  return { valid: true, depth: 1, error: null }
}

// ── 组件 ────────────────────────────────────────────────────────

interface FormulaBuilderProps {
  meta: FormulaMeta | undefined
  metaLoading: boolean
  tokens: string[]
  onTokensChange: (tokens: string[]) => void
  onRun: () => void
  isRunning: boolean
  error: string | null
}

const GROUP_COLORS: Record<string, string> = {
  动量:    "text-[#3fb950] border-[#3fb950]/30 hover:bg-[#3fb950]/10",
  振荡:    "text-[#58a6ff] border-[#58a6ff]/30 hover:bg-[#58a6ff]/10",
  均值回归: "text-[#bc8cff] border-[#bc8cff]/30 hover:bg-[#bc8cff]/10",
  波动率:  "text-[#e3b341] border-[#e3b341]/30 hover:bg-[#e3b341]/10",
  成交量:  "text-[#f78166] border-[#f78166]/30 hover:bg-[#f78166]/10",
  常量:    "text-[#8b949e] border-[#30363d] hover:bg-[#21262d]",
}

const OP_GROUP_COLOR = "text-[#e6edf3] border-[#484f58] hover:bg-[#30363d]"

export function FormulaBuilder({
  meta, metaLoading, tokens, onTokensChange, onRun, isRunning, error,
}: FormulaBuilderProps) {
  const featureNames = useMemo(
    () => new Set((meta?.features ?? []).map((f) => f.name)),
    [meta],
  )
  const opArity = useMemo(() => {
    const m = new Map<string, number>()
    for (const op of meta?.operators ?? []) m.set(op.name, op.arity)
    return m
  }, [meta])

  const stack = useMemo(
    () => validateTokens(tokens, featureNames, opArity),
    [tokens, featureNames, opArity],
  )

  const groupedFeatures = useMemo(() => {
    const g: Record<string, FormulaFeature[]> = {}
    for (const f of meta?.features ?? []) {
      (g[f.group] ??= []).push(f)
    }
    return g
  }, [meta])

  const groupedOps = useMemo(() => {
    const g: Record<string, FormulaOperator[]> = {}
    for (const op of meta?.operators ?? []) {
      (g[op.group] ??= []).push(op)
    }
    return g
  }, [meta])

  function pushToken(tok: string) {
    onTokensChange([...tokens, tok])
  }
  function popToken() {
    onTokensChange(tokens.slice(0, -1))
  }
  function clearTokens() {
    onTokensChange([])
  }
  function loadPreset(preset: FormulaPreset) {
    onTokensChange([...preset.tokens])
  }

  if (metaLoading) {
    return <div className="card flex justify-center py-8"><Spinner /></div>
  }

  return (
    <div className="space-y-4">
      {/* ── 公式显示区 ── */}
      <div className="card">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-[#e6edf3]">公式（逆波兰 RPN）</h3>
          <div className="flex items-center gap-2">
            <span className={`text-[10px] px-2 py-0.5 rounded border ${
              stack.valid
                ? "text-[#3fb950] border-[#3fb950]/30 bg-[#162a1e]"
                : "text-[#e3b341] border-[#e3b341]/30 bg-[#272111]"
            }`}>
              {stack.valid ? "✓ 公式有效" : tokens.length === 0 ? "空公式" : `栈深 ${stack.depth}`}
            </span>
          </div>
        </div>

        {/* Token 序列 */}
        <div className="min-h-[52px] rounded-lg border border-[#30363d] bg-[#0d1117] p-2 flex flex-wrap gap-1.5 items-center">
          {tokens.length === 0 ? (
            <span className="text-xs text-[#6e7681] px-1">
              从下方点选「特征」和「算子」构建公式，或加载预设示例
            </span>
          ) : (
            tokens.map((tok, i) => {
              const isFeature = featureNames.has(tok)
              return (
                <span key={i}
                  className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-mono border ${
                    isFeature
                      ? "text-[#58a6ff] border-[#58a6ff]/30 bg-[#0d1a2b]"
                      : "text-[#e6edf3] border-[#484f58] bg-[#21262d]"
                  }`}>
                  <span className="text-[9px] text-[#6e7681]">{i + 1}</span>
                  {tok}
                </span>
              )
            })
          )}
        </div>

        {/* 提示 & 操作 */}
        <div className="flex items-center justify-between mt-2">
          <p className="text-[10px] text-[#6e7681]">
            {stack.error ? <span className="text-[#e3b341]">{stack.error}</span> : "逆波兰：操作数在前，算子在后，如 MOM20 ATR_RATIO DIV = 动量/波动率"}
          </p>
          <div className="flex gap-1.5">
            <button onClick={popToken} disabled={!tokens.length}
              className="text-[10px] px-2 py-1 rounded border border-[#30363d] text-[#8b949e] hover:text-[#e6edf3] disabled:opacity-40 transition-colors">
              ← 撤销
            </button>
            <button onClick={clearTokens} disabled={!tokens.length}
              className="text-[10px] px-2 py-1 rounded border border-[#f85149]/30 text-[#f85149] hover:bg-[#f85149]/10 disabled:opacity-40 transition-colors">
              清空
            </button>
          </div>
        </div>

        {/* 运行按钮 */}
        <button
          onClick={onRun}
          disabled={isRunning || !stack.valid}
          className="btn btn-primary w-full mt-3 disabled:opacity-50">
          {isRunning ? <Spinner size="sm" className="mx-auto" /> : "▶ 运行公式因子分析"}
        </button>
        {error && <p className="text-xs text-[#f85149] mt-2 leading-snug">{error}</p>}
      </div>

      {/* ── 预设公式 ── */}
      <div className="card">
        <h3 className="text-xs font-semibold text-[#8b949e] mb-2">💡 预设公式（点击加载）</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {(meta?.presets ?? []).map((preset) => (
            <button key={preset.name} onClick={() => loadPreset(preset)}
              className="text-left p-2.5 rounded-lg border border-[#30363d] bg-[#0d1117] hover:border-[#58a6ff]/40 hover:bg-[#111d2e] transition-colors group">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium text-[#e6edf3]">{preset.name}</span>
                <span className="text-[9px] text-[#58a6ff] opacity-0 group-hover:opacity-100 transition-opacity">加载 →</span>
              </div>
              <p className="text-[10px] text-[#6e7681] leading-snug mb-1.5">{preset.desc}</p>
              <code className="text-[9px] text-[#8b949e] font-mono">{preset.tokens.join(" ")}</code>
            </button>
          ))}
        </div>
      </div>

      {/* ── 特征面板 ── */}
      <div className="card">
        <h3 className="text-xs font-semibold text-[#8b949e] mb-2">基础特征（操作数）</h3>
        <div className="space-y-2.5">
          {Object.entries(groupedFeatures).map(([group, feats]) => (
            <div key={group}>
              <p className="text-[10px] text-[#6e7681] mb-1">{group}</p>
              <div className="flex flex-wrap gap-1.5">
                {feats.map((f) => (
                  <button key={f.name} onClick={() => pushToken(f.name)}
                    title={f.label}
                    className={`text-[11px] font-mono px-2 py-1 rounded border transition-colors ${
                      GROUP_COLORS[group] ?? "text-[#8b949e] border-[#30363d] hover:bg-[#21262d]"
                    }`}>
                    {f.name}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── 算子面板 ── */}
      <div className="card">
        <h3 className="text-xs font-semibold text-[#8b949e] mb-2">算子（运算符）</h3>
        <div className="space-y-2.5">
          {Object.entries(groupedOps).map(([group, ops]) => (
            <div key={group}>
              <p className="text-[10px] text-[#6e7681] mb-1">{group}</p>
              <div className="flex flex-wrap gap-1.5">
                {ops.map((op) => {
                  const canApply = stack.depth >= op.arity
                  return (
                    <button key={op.name} onClick={() => pushToken(op.name)}
                      title={`${op.label}（需 ${op.arity} 个操作数）`}
                      className={`text-[11px] font-mono px-2 py-1 rounded border transition-colors ${OP_GROUP_COLOR} ${
                        canApply ? "" : "opacity-40"
                      }`}>
                      {op.name}
                      <span className="ml-1 text-[9px] text-[#6e7681]">{op.arity}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
        <p className="text-[9px] text-[#6e7681] mt-2">数字表示算子所需操作数个数（arity）。灰色表示当前栈深不足。</p>
      </div>
    </div>
  )
}
