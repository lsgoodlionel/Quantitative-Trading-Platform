import { AiSetupNotice } from "@/components/ai/AiSetupNotice"
import { Spinner } from "@/components/ui/Spinner"
import {
  SEVERITY_LABELS,
  SEVERITY_TONES,
  formatStamp,
  useBacktestDiagnosis,
} from "@/hooks/useAiReports"
import type { FullValidationResult } from "@/hooks/useFullValidation"

// ── 回测 AI 诊断（V3 Wave C-a / I5）───────────────────────────────
//
// AI 在这里**解读**规则评级，不取代它。所以本面板始终挂在规则化评级卡片下方，
// 而不是替代它显示 —— 用户永远先看到可审计的判据，再看到人话翻译。
//
// 模型输出与规则判据矛盾时，后端会打 `has_contradiction`，这里用红条挑明，
// 而不是把矛盾的文字原样端出去。

interface BacktestDiagnosisPanelProps {
  result: FullValidationResult
}

export function BacktestDiagnosisPanel({ result }: BacktestDiagnosisPanelProps) {
  const diagnosis = useBacktestDiagnosis()
  const data = diagnosis.data

  function handleDiagnose() {
    diagnosis.mutate({
      run_id: result.run_id,
      // run_id 从不落库，后端反查不到 —— 结果体本身就是唯一可靠的输入
      grade: result.grade as unknown as Record<string, unknown>,
      steps: result.steps as Record<string, Record<string, unknown>>,
    })
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-[#e6edf3]">AI 诊断</h3>
        <span className="text-[11px] text-[#6e7681]">
          把上面的规则判据翻译成人能读的诊断与下一步，不会推翻评级
        </span>
        <button
          type="button"
          className="btn btn-primary text-xs ml-auto"
          disabled={diagnosis.isPending}
          onClick={handleDiagnose}
        >
          {diagnosis.isPending ? "诊断中…" : "🤖 生成 AI 诊断"}
        </button>
      </div>

      <AiSetupNotice error={diagnosis.error} />

      {diagnosis.isPending && (
        <div className="flex items-center gap-2 text-xs text-[#8b949e]">
          <Spinner size="sm" />
          正在解读验证结果…
        </div>
      )}

      {data && !diagnosis.isPending && (
        <div className="space-y-3">
          {data.has_contradiction && (
            <div className="rounded border border-[#f85149]/50 bg-[#2a1b1b] px-3 py-2 space-y-1.5">
              <p className="text-xs font-semibold text-[#f85149]">
                ⚠️ AI 解读与规则判据存在冲突，请以规则判据为准
              </p>
              {data.contradictions.map((item) => (
                <p key={item.rule} className="text-[11px] text-[#c9d1d9] leading-relaxed">
                  {item.detail}
                </p>
              ))}
            </div>
          )}

          <div className="flex items-center gap-3 flex-wrap text-[11px] text-[#8b949e]">
            <span className="font-mono">{data.coverage}</span>
            <span className="font-mono">
              评级 {data.grade_level} · {data.grade_score}/100
            </span>
            {!data.is_complete && (
              <span className="text-[#d29922]">未覆盖全部 5 步，结论只反映已完成的检查</span>
            )}
          </div>

          <p className="text-xs text-[#c9d1d9] leading-relaxed whitespace-pre-line">
            {data.summary}
          </p>

          {data.findings.length > 0 && (
            <ul className="space-y-2">
              {data.findings.map((finding, index) => (
                <li
                  key={`${finding.title}-${index}`}
                  className={`bg-[#161b22] border rounded px-3 py-2 ${
                    SEVERITY_TONES[finding.severity] ?? SEVERITY_TONES.medium
                  }`}
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-[#e6edf3]">{finding.title}</span>
                    {finding.rule && (
                      <span className="text-[10px] font-mono text-[#6e7681]">{finding.rule}</span>
                    )}
                    <span className="text-[10px] font-mono ml-auto">
                      严重度 {SEVERITY_LABELS[finding.severity] ?? finding.severity}
                    </span>
                  </div>
                  <p className="text-[11px] text-[#c9d1d9] mt-1 leading-relaxed">
                    {finding.detail}
                  </p>
                </li>
              ))}
            </ul>
          )}

          {data.next_steps.length > 0 && (
            <div>
              <h4 className="text-xs text-[#8b949e] mb-1.5">下一步</h4>
              <ol className="space-y-1 list-decimal list-inside">
                {data.next_steps.map((step, index) => (
                  <li key={`${step}-${index}`} className="text-[11px] text-[#c9d1d9]">
                    {step}
                  </li>
                ))}
              </ol>
            </div>
          )}

          <p className="text-[10px] text-[#6e7681] leading-relaxed border-t border-[#21262d] pt-3">
            {data.disclaimer}
          </p>
          <p className="text-[10px] text-[#6e7681] font-mono">
            模型 {data.model} · 生成于 {formatStamp(data.generated_at)}
          </p>
        </div>
      )}
    </div>
  )
}
