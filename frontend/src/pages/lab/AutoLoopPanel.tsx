import { useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { AutoLoopRoundView } from "@/pages/lab/AutoLoopRoundView"
import {
  STATUS_LABELS,
  isPending,
  useAutoLoopRound,
  useAutoLoopRounds,
  useStartAutoLoop,
  type AutoLoopRequest,
  type LoopRound,
} from "@/hooks/useAutoFactorLoop"

const DEFAULT_UNIVERSE = "AAPL, MSFT, NVDA, GOOGL, AMZN"

/**
 * 自动因子研发循环（V3 · I2）
 *
 * 启动表单 → 轮次列表 → 单轮结果。一轮以分钟计，走异步任务 + 轮询。
 */
export function AutoLoopPanel() {
  const { toast } = useToast()
  const [form, setForm] = useState({
    universe: DEFAULT_UNIVERSE,
    isEnd: "",
    market: "US" as AutoLoopRequest["market"],
    generations: 5,
    population: 40,
    topK: 5,
    maxCandidates: 2000,
  })
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const listQ = useAutoLoopRounds()
  const roundQ = useAutoLoopRound(selectedId)
  const startM = useStartAutoLoop()

  const rounds = listQ.data?.items ?? []

  const handleStart = () => {
    const universe = form.universe
      .split(/[,\s]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean)

    if (universe.length < 3) {
      toast("横截面搜索至少需要 3 个标的", "error")
      return
    }
    if (!form.isEnd) {
      toast("请填写样本内截止日 —— 没有它就没有样本外验证", "error")
      return
    }

    startM.mutate(
      {
        universe,
        is_end: form.isEnd,
        market: form.market,
        generations: form.generations,
        population: form.population,
        top_k: form.topK,
        max_candidates_evaluated: form.maxCandidates,
      },
      {
        onSuccess: (res) => {
          setSelectedId(res.round_id)
          toast("已提交，一轮搜索通常需要几分钟", "success")
        },
        onError: (e) => toast(`启动失败: ${e.message}`, "error"),
      },
    )
  }

  return (
    <section className="space-y-4" aria-labelledby="auto-loop-heading">
      <div className="card space-y-3">
        <div>
          <h3 id="auto-loop-heading" className="text-sm font-semibold text-[#e6edf3]">
            自动因子研发循环
          </h3>
          <p className="mt-1 text-[11px] leading-relaxed text-[#6e7681]">
            种子表达式 → 遗传搜索 → 成本感知适应度 → 样本外验证 → 入库 → 模型复盘。
            <strong className="text-[#8b949e]">
              「样本内截止日」之后的数据在整个搜索过程中不可见
            </strong>
            ；结果只入库，不会自动上线。
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="标的（逗号分隔，≥3）" className="sm:col-span-2">
            <input
              className="input w-full text-sm"
              value={form.universe}
              onChange={(e) => setForm({ ...form, universe: e.target.value })}
            />
          </Field>
          <Field label="样本内截止日">
            <input
              type="date"
              aria-label="样本内截止日"
              className="input w-full text-sm"
              value={form.isEnd}
              onChange={(e) => setForm({ ...form, isEnd: e.target.value })}
            />
          </Field>
          <Field label="市场">
            <select
              aria-label="市场"
              className="input w-full text-sm"
              value={form.market}
              onChange={(e) =>
                setForm({ ...form, market: e.target.value as AutoLoopRequest["market"] })
              }
            >
              <option value="US">美股</option>
              <option value="HK">港股</option>
              <option value="A">A股</option>
            </select>
          </Field>
          <NumberField
            label="代数"
            value={form.generations}
            onChange={(generations) => setForm({ ...form, generations })}
          />
          <NumberField
            label="种群规模"
            value={form.population}
            onChange={(population) => setForm({ ...form, population })}
          />
          <NumberField
            label="保留因子数"
            value={form.topK}
            onChange={(topK) => setForm({ ...form, topK })}
          />
          <NumberField
            label="评估上限"
            value={form.maxCandidates}
            onChange={(maxCandidates) => setForm({ ...form, maxCandidates })}
          />
        </div>

        <button
          className="btn btn-primary text-sm"
          onClick={handleStart}
          disabled={startM.isPending}
        >
          {startM.isPending ? "提交中…" : "启动一轮"}
        </button>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="xl:col-span-1">
          <RoundList
            rounds={rounds}
            isLoading={listQ.isLoading}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </div>
        <div className="xl:col-span-2">
          <RoundDetail round={roundQ.data} isLoading={roundQ.isLoading} />
        </div>
      </div>
    </section>
  )
}

interface RoundListProps {
  rounds: LoopRound[]
  isLoading: boolean
  selectedId: string | null
  onSelect: (id: string) => void
}

function RoundList({ rounds, isLoading, selectedId, onSelect }: RoundListProps) {
  if (isLoading) {
    return (
      <div className="card flex h-32 items-center justify-center">
        <Spinner size="sm" />
      </div>
    )
  }
  if (rounds.length === 0) {
    return <p className="card text-xs text-[#6e7681]">还没有跑过任何一轮。</p>
  }

  return (
    <ul className="card space-y-1 p-2">
      {rounds.map((r) => (
        <li key={r.round_id}>
          <button
            onClick={() => onSelect(r.round_id)}
            aria-pressed={selectedId === r.round_id}
            className={`w-full rounded-md border px-2.5 py-2 text-left transition-colors ${
              selectedId === r.round_id
                ? "border-[#58a6ff] bg-[#1f6feb]/20"
                : "border-transparent hover:border-[#30363d]"
            }`}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-mono text-[11px] text-[#e6edf3]">{r.round_id}</span>
              <span
                className={`text-[10px] ${
                  r.status === "error" ? "text-[#f85149]" : "text-[#8b949e]"
                }`}
              >
                {STATUS_LABELS[r.status]}
              </span>
            </div>
            <p className="mt-0.5 text-[10px] text-[#6e7681]">
              {new Date(r.created_at * 1000).toLocaleString("zh-CN")}
              {r.result && ` · 检验 ${r.result.hypotheses_tested} 个假设`}
            </p>
          </button>
        </li>
      ))}
    </ul>
  )
}

function RoundDetail({ round, isLoading }: { round?: LoopRound; isLoading: boolean }) {
  if (!round) {
    return (
      <div className="card flex h-32 items-center justify-center text-sm text-[#6e7681]">
        {isLoading ? <Spinner size="sm" /> : "选择左侧任一轮次查看结果"}
      </div>
    )
  }
  if (isPending(round.status)) {
    return (
      <div className="card flex h-32 flex-col items-center justify-center gap-2 text-sm text-[#8b949e]">
        <Spinner size="sm" />
        <span>{STATUS_LABELS[round.status]}，一轮通常需要几分钟…</span>
      </div>
    )
  }
  if (round.status === "error" || !round.result) {
    return (
      <div className="card text-sm text-[#f85149]">
        本轮失败：{round.error ?? "未知原因"}
      </div>
    )
  }
  return <AutoLoopRoundView result={round.result} />
}

function Field({
  label,
  className = "",
  children,
}: {
  label: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1 block text-[10px] text-[#6e7681]">{label}</span>
      {children}
    </label>
  )
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string
  value: number
  onChange: (value: number) => void
}) {
  return (
    <Field label={label}>
      <input
        type="number"
        aria-label={label}
        className="input w-full text-sm"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </Field>
  )
}
