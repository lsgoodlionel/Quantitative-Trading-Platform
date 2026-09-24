import { useSaveBacktestHistory } from "@/hooks/useBacktestHistory"
import { Spinner } from "@/components/ui/Spinner"
import type { BacktestRequest, BacktestResult } from "@/types"

interface SaveResultButtonProps {
  result: BacktestResult
  form: BacktestRequest
}

// ── 保存回测结果（V3 · H5）─────────────────────────────────────
// 落 TimescaleDB：无条数上限、刷新不丢；净值曲线降采样后整条保存。
export function SaveResultButton({ result, form }: SaveResultButtonProps) {
  const { mutate: save, isPending, isSuccess, error } = useSaveBacktestHistory()

  function handleSave() {
    save({
      strategy_name: form.strategy_name,
      symbol: form.symbol,
      market: form.market,
      frequency: form.frequency,
      start_date: form.start_date,
      end_date: form.end_date,
      initial_cash: form.initial_cash,
      final_value: result.final_value,
      params: form.params ?? {},
      metrics: result.metrics as unknown as Record<string, number>,
      equity_curve: result.equity_curve,
    })
  }

  return (
    <div className="card flex items-center gap-3 py-3">
      <button type="button" className="btn btn-ghost text-xs" disabled={isPending} onClick={handleSave}>
        {isPending ? <Spinner size="sm" /> : "💾 保存结果"}
      </button>
      {isSuccess && <span className="text-[11px] text-[#3fb950]">已保存，可在「🗂 历史」中查看、重跑或对比。</span>}
      {error && <span className="text-[11px] text-[#f85149]">保存失败：{error.message}</span>}
      {!isSuccess && !error && (
        <span className="text-[11px] text-[#6e7681]">保存后刷新不丢；历史无条数上限，仅支持手动删除。</span>
      )}
    </div>
  )
}
