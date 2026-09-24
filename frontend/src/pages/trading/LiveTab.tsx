// 策略交易 Tab（原 /live-strategy 整页）：模拟盘实例列表 + 启动/调参表单。
// 由 pages/LiveStrategy.tsx 迁入交易页的一个 Tab（V3 · H1），逻辑未改。
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { EmptyState } from "@/components/ui/EmptyState"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { useStrategies } from "@/hooks/useBacktest"
import {
  useLiveStrategies, useStopStrategy, useDeleteStrategyInstance,
} from "@/hooks/useLiveStrategy"
import type { Market, Frequency } from "@/types"
import { InstanceCard } from "./live/InstanceCard"
import { LaunchForm } from "./live/LaunchForm"
import type { RerunValues } from "./live/shared"

export function LiveTab() {
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: instances, isLoading } = useLiveStrategies()
  const { data: strategies } = useStrategies()
  const { mutate: stopStrategy, isPending: isStopping, variables: stoppingId } = useStopStrategy()
  const { mutate: deleteInstance } = useDeleteStrategyInstance()
  const [showForm, setShowForm] = useState(false)
  const [rerunValues, setRerunValues] = useState<RerunValues | undefined>()

  const running = (instances ?? []).filter((i) => i.state === "running").length

  // 从回测页面携带参数进入时自动打开表单
  useEffect(() => {
    const strategy = searchParams.get("strategy")
    if (!strategy) return
    let params: Record<string, unknown> = {}
    try { params = JSON.parse(searchParams.get("params") ?? "{}") } catch { /* URL 参数畸形（用户手改/截断）：退回空参数，用策略默认值 */ }
    setRerunValues({
      strategy_name: strategy,
      symbol:   searchParams.get("symbol")   ?? "AAPL",
      market:   (searchParams.get("market")  ?? "US") as Market,
      frequency:(searchParams.get("freq")    ?? "1d") as Frequency,
      params,
      sim_days: 60,
    })
    setShowForm(true)
    // 清空已消费的参数，避免刷新重复弹出。
    // 注意只清这几个键：?tab= 是页面自己的 Tab 状态，清掉会把用户弹回默认 Tab。
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      for (const key of ["strategy", "symbol", "market", "freq", "params"]) next.delete(key)
      return next
    }, { replace: true })
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  function handleRerun(values: RerunValues) {
    setRerunValues(values)
    setShowForm(true)
    window.scrollTo({ top: 0, behavior: "smooth" })
  }

  function handleCloseForm() {
    setShowForm(false)
    setRerunValues(undefined)
  }

  return (
    <>
      {/* 功能说明横幅 */}
      <div className="flex items-start gap-3 mb-5 px-4 py-3 rounded-xl border border-[#58a6ff]/20 bg-[#0d1421]">
        <span className="text-xl mt-0.5">🤖</span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-[#e6edf3] mb-1">量化策略自动执行</p>
          <p className="text-xs text-[#8b949e] leading-relaxed">
            在此选择策略、标的和参数，系统将在历史数据上运行<strong className="text-[#e6edf3]">模拟盘</strong>（回放真实行情，本地纸面撮合），
            看到净值曲线和成交记录。策略满意后，可在
            <Link to="/trading?tab=orders" className="text-[#58a6ff] underline mx-1">订单中心</Link>
            手动下单，或等待 Alpaca 实盘接入功能上线。
          </p>
        </div>
        <div className="shrink-0 flex gap-2">
          <Link to="/trading?tab=orders"
            className="px-3 py-1.5 rounded text-xs border border-[#3fb950]/30 text-[#3fb950] hover:bg-[#3fb950]/10 transition-colors whitespace-nowrap">
            📋 手动下单
          </Link>
        </div>
      </div>

      {/* 头部操作行 */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">策略实例</h2>
          <p className="text-xs text-[#6e7681] mt-0.5">
            {running > 0 ? `${running} 个运行中 · ` : ""}
            可自由调整参数和模拟天数进行多次对比
          </p>
        </div>
        <button
          onClick={() => { setRerunValues(undefined); setShowForm(!showForm) }}
          className="btn btn-primary text-xs px-4">
          {showForm && !rerunValues ? "取消" : "+ 新建模拟"}
        </button>
      </div>

      {showForm && strategies && (
        <div className="mb-6">
          <LaunchForm
            strategies={strategies}
            onClose={handleCloseForm}
            initialValues={rerunValues}
          />
        </div>
      )}

      {isLoading && <div className="flex justify-center py-20"><Spinner size="lg" /></div>}

      {!isLoading && (!instances || instances.length === 0) && (
        <EmptyState
          title="尚无模拟盘"
          description='点击「+ 新建模拟」，选择策略和标的，系统会立即生成模拟结果，包含净值曲线、成交记录和操作建议。模拟结束后可点击「⚙ 调整重跑」修改参数或延长天数进行对比。'
        />
      )}

      {instances && instances.length > 0 && (
        <div className="space-y-4">
          {[...instances]
            .sort((a, b) => (a.state === "running" ? -1 : 1) - (b.state === "running" ? -1 : 1))
            .map((inst) => (
              <InstanceCard
                key={inst.instance_id}
                inst={inst}
                onRerun={handleRerun}
                onStop={(id) => stopStrategy(id, {
                  onSuccess: () => toast("已停止", "success"),
                  onError: (e) => toast(e.message, "error"),
                })}
                onDelete={(id) => deleteInstance(id, {
                  onSuccess: () => toast("已删除", "success"),
                  onError: (e) => toast(e.message, "error"),
                })}
                isStopping={isStopping && stoppingId === inst.instance_id}
              />
            ))}
        </div>
      )}
    </>
  )
}
