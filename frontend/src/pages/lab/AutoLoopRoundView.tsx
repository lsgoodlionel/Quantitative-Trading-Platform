import {
  formatDecay,
  formatMetric,
  type LoopResult,
  type LoopSurvivor,
} from "@/hooks/useAutoFactorLoop"

/**
 * 单轮结果：存活因子 + 样本内/外 IC 并排 + 检验假设数 + LLM 复盘
 *
 * 三个不能省的展示约定：
 * 1. 样本内与样本外指标**并排成列**，绝不合并成一个「综合分」——
 *    衰减幅度本身就是用户要看的东西。
 * 2. `hypotheses_tested` 与因子指标**同屏**，并附后端给的那句说明。
 *    只给指标不给分母，等于把多重检验的结果当成发现在展示。
 * 3. 过拟合嫌疑只标注、不隐藏。被标了的因子照样列出来。
 */
export function AutoLoopRoundView({ result }: { result: LoopResult }) {
  return (
    <div className="space-y-4">
      <DenominatorBanner result={result} />
      {!result.out_of_sample_available && <NoOutOfSampleWarning />}
      {result.artifact_error && (
        <p className="rounded-md border border-[#f85149]/30 bg-[#2a1b1b]/40 px-3 py-2 text-xs text-[#f85149]">
          结果未能写入产物库：{result.artifact_error}
        </p>
      )}

      <SurvivorTable survivors={result.survivors} />

      <ReviewCard result={result} />
    </div>
  )
}

function DenominatorBanner({ result }: { result: LoopResult }) {
  return (
    <div className="rounded-lg border border-[#e3b341]/30 bg-[#272111]/40 p-3">
      <div className="mb-1.5 flex items-baseline gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-[#e3b341]">
          检验了多少个假设
        </span>
        <span className="font-mono text-lg font-bold text-[#e6edf3]">
          {result.hypotheses_tested.toLocaleString()}
        </span>
        {result.truncated && (
          <span className="rounded bg-[#e3b341]/15 px-1.5 py-0.5 text-[10px] font-bold text-[#e3b341]">
            已达评估上限，提前停止
          </span>
        )}
      </div>
      {/* 后端给的措辞，如实照抄 */}
      <p className="text-[11px] leading-relaxed text-[#8b949e]">
        {result.multiple_testing_note}
      </p>
      <p className="mt-1.5 text-[10px] text-[#6e7681]">
        样本内截止 {result.is_end}（其后 {result.embargo_bars} 根 bar 为禁运期，
        标签会偷看未来价格，故一并排除）
        {result.artifact_id && ` · 已入库产物 ${result.artifact_id.slice(0, 8)}…`}
      </p>
    </div>
  )
}

function NoOutOfSampleWarning() {
  return (
    <p className="rounded-md border border-[#f85149]/30 bg-[#2a1b1b]/40 px-3 py-2 text-xs text-[#f85149]">
      本轮没有样本外数据，下表只有样本内指标。遗传搜索天然会过拟合它评估的那段样本，
      这些数字度量的是搜索强度，不是预测能力 —— 请把 is_end 往前调再跑一轮。
    </p>
  )
}

function SurvivorTable({ survivors }: { survivors: LoopSurvivor[] }) {
  if (survivors.length === 0) {
    return (
      <p className="card text-xs text-[#8b949e]">
        本轮没有任何因子通过样本内评估。
      </p>
    )
  }

  return (
    <div className="card overflow-x-auto p-0">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[#21262d] text-[10px] uppercase text-[#8b949e]">
            <th className="py-2 pl-4 pr-3 text-left">表达式</th>
            <th className="py-2 pr-3 text-right">样本内 IC</th>
            <th className="py-2 pr-3 text-right">样本外 IC</th>
            <th className="py-2 pr-3 text-right">留存</th>
            <th className="py-2 pr-3 text-right">样本内 RankIC</th>
            <th className="py-2 pr-3 text-right">样本外 RankIC</th>
            <th className="py-2 pr-4 text-left">标注</th>
          </tr>
        </thead>
        <tbody>
          {survivors.map((s) => (
            <tr key={s.expr} className="border-b border-[#21262d]/60 last:border-0">
              <td className="py-2 pl-4 pr-3 font-mono text-[10px] text-[#e6edf3]">
                {s.expr}
              </td>
              <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">
                {formatMetric(s.is_ic_mean)}
              </td>
              <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">
                {s.out_of_sample_evaluated ? formatMetric(s.oos_ic_mean) : "无数据"}
              </td>
              <td
                className={`py-2 pr-3 text-right font-mono ${
                  s.overfit_suspect ? "text-[#f85149]" : "text-[#3fb950]"
                }`}
              >
                {formatDecay(s.ic_decay_ratio)}
              </td>
              <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">
                {formatMetric(s.is_rank_ic_mean)}
              </td>
              <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">
                {s.out_of_sample_evaluated ? formatMetric(s.oos_rank_ic_mean) : "无数据"}
              </td>
              <td className="py-2 pr-4">
                {s.overfit_suspect && (
                  <span
                    className="rounded bg-[#f85149]/15 px-1.5 py-0.5 text-[10px] font-bold text-[#f85149]"
                    title="样本外 IC 不足样本内的一半。结果照常保留 —— 看到衰减幅度比替你丢掉更有价值。"
                  >
                    过拟合嫌疑
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ReviewCard({ result }: { result: LoopResult }) {
  return (
    <div className="card space-y-2">
      <h4 className="text-sm font-semibold text-[#e6edf3]">模型复盘</h4>
      {result.llm_review ? (
        <p className="whitespace-pre-wrap text-xs leading-relaxed text-[#8b949e]">
          {result.llm_review}
        </p>
      ) : (
        <p className="text-xs text-[#6e7681]">
          本轮没有模型复盘
          {result.llm_error ? `（${result.llm_error}）` : "（未配置模型服务，不影响搜索结果）"}。
        </p>
      )}

      {result.next_seeds.length > 0 && (
        <div className="pt-1">
          <p className="mb-1 text-[10px] text-[#6e7681]">
            模型为下一轮提出的种子（已过算子白名单校验，不会自动带入下一轮）
            {result.rejected_seed_count > 0 &&
              ` · 另有 ${result.rejected_seed_count} 条因非法被丢弃`}
          </p>
          <div className="flex flex-wrap gap-1">
            {result.next_seeds.map((seed) => (
              <span
                key={seed}
                className="rounded border border-[#30363d] px-1.5 py-0.5 font-mono text-[10px] text-[#8b949e]"
              >
                {seed}
              </span>
            ))}
          </div>
        </div>
      )}

      <p className="border-t border-[#21262d] pt-2 text-[10px] text-[#6e7681]">
        循环只把因子写进产物库，不会自动注册为策略、更不会接实盘。
        要用它交易，请在实验记录里人工晋级。
      </p>
    </div>
  )
}
