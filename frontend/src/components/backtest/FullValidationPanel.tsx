import { EmptyState } from "@/components/ui/EmptyState"
import { Spinner } from "@/components/ui/Spinner"
import {
  STEP_LABELS,
  VALIDATION_STEPS,
  type FullValidationResult,
  type ValidationStep,
} from "@/hooks/useFullValidation"

// ── 完整验证综合报告（V3 · H2）─────────────────────────────────
// 评级是规则化启发式汇总而非判决：逐条展示触发依据、未评估项与覆盖度。

const LEVEL_TONES: Record<string, string> = {
  A: "text-[#3fb950] border-[#3fb950]/40 bg-[#3fb950]/5",
  B: "text-[#58a6ff] border-[#58a6ff]/40 bg-[#58a6ff]/5",
  C: "text-[#d29922] border-[#d29922]/40 bg-[#d29922]/5",
  D: "text-[#f85149] border-[#f85149]/40 bg-[#f85149]/5",
}

interface FullValidationPanelProps {
  result: FullValidationResult | null
  isPending: boolean
  error: Error | null
  selectedSteps: ValidationStep[]
  onToggleStep: (step: ValidationStep) => void
}

export function FullValidationPanel({
  result, isPending, error, selectedSteps, onToggleStep,
}: FullValidationPanelProps) {
  return (
    <div className="space-y-4">
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-2">验证步骤</h3>
        <p className="text-[11px] text-[#6e7681] mb-3 leading-relaxed">
          全跑较慢；只想补一步时取消勾选其余步骤即可。某一步失败不会中断其余步骤。
        </p>
        <div className="flex flex-wrap gap-2">
          {VALIDATION_STEPS.map((step) => {
            const active = selectedSteps.includes(step)
            return (
              <button key={step} type="button"
                aria-pressed={active}
                className={`text-xs px-3 py-1.5 rounded border transition-colors ${
                  active
                    ? "border-[#58a6ff] text-[#58a6ff] bg-[#58a6ff]/10"
                    : "border-[#21262d] text-[#6e7681] hover:text-[#e6edf3]"
                }`}
                onClick={() => onToggleStep(step)}>
                {STEP_LABELS[step]}
              </button>
            )
          })}
        </div>
      </div>

      {error && (
        <p className="text-[#f85149] text-xs bg-[#2a1b1b] border border-[#f85149]/30 rounded px-3 py-2">
          {error.message}
        </p>
      )}

      {isPending && (
        <div className="card flex items-center justify-center h-48">
          <div className="text-center">
            <Spinner size="lg" className="mx-auto mb-3" />
            <p className="text-[#8b949e] text-sm">串行执行验证步骤中，可能耗时较长…</p>
          </div>
        </div>
      )}

      {!isPending && !result && !error && (
        <div className="card">
          <EmptyState title="点击顶部「⚡ 完整验证」开始"
            description="串行跑 回测 → 寻优 → Walk-Forward → 偏差 → 稳健性，产出规则化综合评级" />
        </div>
      )}

      {result && !isPending && (
        <>
          <GradeCard result={result} />
          <StepsGrid result={result} />
        </>
      )}
    </div>
  )
}

function GradeCard({ result }: { result: FullValidationResult }) {
  const { grade } = result
  const tone = LEVEL_TONES[grade.level] ?? LEVEL_TONES.C

  return (
    <div className={`card border ${tone}`}>
      <div className="flex items-baseline gap-4 flex-wrap">
        <div className={`text-4xl font-bold font-mono ${tone.split(" ")[0]}`}>{grade.level}</div>
        <div className="font-mono text-lg text-[#e6edf3]">{grade.score} / 100</div>
        <div className="text-xs text-[#8b949e]">{grade.level_label}</div>
        <div className="text-xs text-[#8b949e] ml-auto font-mono">{grade.based_on}</div>
      </div>

      {!grade.is_complete && (
        <p className="text-[11px] text-[#d29922] mt-3">
          ⚠️ 本次未覆盖全部 5 步，评级只反映已完成的检查，不代表整体结论。
        </p>
      )}

      {grade.findings.length > 0 ? (
        <ul className="mt-4 space-y-2">
          {grade.findings.map((f) => (
            <li key={f.rule} className="bg-[#161b22] border border-[#21262d] rounded px-3 py-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] font-mono text-[#58a6ff]">{STEP_LABELS[f.step as ValidationStep] ?? f.step}</span>
                <span className="text-xs text-[#e6edf3]">{f.rule}</span>
                <span className="text-[10px] font-mono text-[#f85149] ml-auto">−{f.penalty}</span>
              </div>
              <p className="text-[11px] text-[#c9d1d9] mt-1 leading-relaxed">{f.detail}</p>
              <p className="text-[10px] text-[#6e7681] mt-1 font-mono">
                {f.metric} = {f.value} · 阈值 {f.threshold}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-[#8b949e] mt-4">已完成的检查项均未触发扣分。</p>
      )}

      {grade.not_evaluated.length > 0 && (
        <div className="mt-4">
          <h4 className="text-xs text-[#8b949e] mb-1.5">未评估的规则</h4>
          <ul className="space-y-1">
            {grade.not_evaluated.map((n) => (
              <li key={n.rule} className="text-[11px] text-[#6e7681] font-mono">
                {n.rule} — {n.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-[10px] text-[#6e7681] mt-4 leading-relaxed">{grade.disclaimer}</p>
    </div>
  )
}

function StepsGrid({ result }: { result: FullValidationResult }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {result.requested_steps.map((step) => {
        const payload = result.steps[step] ?? {}
        const failed = typeof payload.error === "string"
        return (
          <div key={step} className={`card border ${failed ? "border-[#f85149]/40" : "border-[#21262d]"}`}>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-semibold text-[#e6edf3]">
                {STEP_LABELS[step as ValidationStep] ?? step}
              </span>
              <span className={`text-[10px] font-mono ml-auto ${failed ? "text-[#f85149]" : "text-[#3fb950]"}`}>
                {failed ? "失败" : "完成"}
              </span>
            </div>
            {failed ? (
              <p className="text-[11px] text-[#f85149] font-mono break-all">{payload.error}</p>
            ) : (
              <pre className="text-[10px] text-[#8b949e] font-mono overflow-x-auto max-h-48">
                {JSON.stringify(payload, null, 1)}
              </pre>
            )}
          </div>
        )
      })}
    </div>
  )
}
