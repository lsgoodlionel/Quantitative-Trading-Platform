import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from "recharts"
import { EmptyState } from "@/components/ui/EmptyState"
import { StatCard } from "./StatCard"
import type {
  TradeAnalytics, TagMetrics, PeriodicStats, PeriodBucket, RiskRatios, TagRow,
} from "@/hooks/useBacktestReport"

interface TradeAnalyticsTabProps {
  analytics: TradeAnalytics | null | undefined
  tagMetrics: TagMetrics | null | undefined
  periodic: PeriodicStats | null | undefined
}

const money = (v: number) => `$${v.toFixed(2)}`
const pct = (v: number) => `${v.toFixed(2)}%`
const num2 = (v: number) => v.toFixed(2)
const num3 = (v: number) => v.toFixed(3)

// ── 扩展风险比率分组配置 ────────────────────────────────────────
interface RatioSpec {
  key: keyof RiskRatios
  label: string
  fmt: (v: number) => string
  help: string
}
interface RatioGroup { title: string; rows: RatioSpec[] }

const RATIO_GROUPS: RatioGroup[] = [
  {
    title: "风险调整",
    rows: [
      { key: "cagr_pct", label: "CAGR", fmt: pct, help: "(净值末/净值初)^(365/天数) − 1" },
      { key: "serenity_index", label: "Serenity 指数", fmt: num3, help: "Σ收益 / (溃疡指数 × pitfall)" },
      { key: "recovery_factor", label: "恢复因子", fmt: num2, help: "净利润 / |最大回撤金额|" },
      { key: "gain_to_pain_ratio", label: "盈亏痛苦比", fmt: num3, help: "Σ收益 / |Σ负收益|" },
      { key: "kelly_criterion", label: "凯利值", fmt: num3, help: "胜率 − (1−胜率)/赔率" },
    ],
  },
  {
    title: "回撤 / 尾部",
    rows: [
      { key: "ulcer_index", label: "溃疡指数", fmt: num3, help: "√(Σ回撤² / (N−1))" },
      { key: "value_at_risk_95_pct", label: "VaR 95%", fmt: pct, help: "日收益分布 5% 分位数" },
      { key: "cvar_95_pct", label: "CVaR 95%", fmt: pct, help: "低于 5% 分位数的平均收益（预期损失）" },
      { key: "max_underwater_days", label: "最长水下天数", fmt: (v) => `${v}d`, help: "从峰值到再创新高的最长跨度" },
      { key: "tail_ratio", label: "尾部比率", fmt: num3, help: "p95(收益) / |p5(收益)|" },
      { key: "common_sense_ratio", label: "常识比率", fmt: num3, help: "尾部比率 × 盈亏因子" },
      { key: "downside_deviation_pct", label: "下行偏差", fmt: pct, help: "负收益的年化标准差" },
    ],
  },
  {
    title: "分布",
    rows: [
      { key: "skew", label: "偏度", fmt: num3, help: "收益分布 3 阶标准矩" },
      { key: "kurtosis", label: "峰度", fmt: num3, help: "超额峰度（Fisher）" },
      { key: "avg_up_month_pct", label: "平均上涨月", fmt: pct, help: "月收益 >0 的均值" },
      { key: "avg_down_month_pct", label: "平均下跌月", fmt: pct, help: "月收益 <0 的均值" },
    ],
  },
  {
    title: "交易质量",
    rows: [
      { key: "payoff_ratio", label: "赔率", fmt: num3, help: "平均盈利 / |平均亏损|" },
      { key: "best_trade_pct", label: "最佳交易", fmt: pct, help: "回合收益率最大值" },
      { key: "worst_trade_pct", label: "最差交易", fmt: pct, help: "回合收益率最小值" },
      { key: "avg_holding_period_days", label: "平均持仓", fmt: (v) => `${v.toFixed(1)}d`, help: "回合平均持仓天数" },
      { key: "win_rate_long_pct", label: "多头胜率", fmt: pct, help: "多头盈利 / 多头总数" },
      { key: "win_rate_short_pct", label: "空头胜率", fmt: pct, help: "空头盈利 / 空头总数" },
      { key: "profit_factor_long", label: "多头盈亏因子", fmt: num3, help: "多头总盈利 / |多头总亏损|" },
      { key: "profit_factor_short", label: "空头盈亏因子", fmt: num3, help: "空头总盈利 / |空头总亏损|" },
    ],
  },
]

export function TradeAnalyticsTab({ analytics, tagMetrics, periodic }: TradeAnalyticsTabProps) {
  if (!analytics || analytics.total_trades < 2) {
    return (
      <div className="card">
        <EmptyState
          title="暂无逐笔分析数据"
          description="回合交易不足（至少需要 2 笔完整开平仓）"
        />
      </div>
    )
  }

  const a = analytics
  return (
    <div className="space-y-4">
      {/* 1. 汇总指标 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <StatCard label="总/胜/负" value={`${a.total_trades}/${a.won}/${a.lost}`} sub={`平手 ${a.breakeven}`} />
        <StatCard label="胜率" value={pct(a.win_rate_pct)} accent={a.win_rate_pct >= 50 ? "up" : "down"} />
        <StatCard label="盈亏均值比" value={num3(a.ratio_avg_win_loss)} help="平均盈利 / |平均亏损|" />
        <StatCard label="净利润" value={money(a.net_profit)} accent={a.net_profit >= 0 ? "up" : "down"} />
        <StatCard label="最大盈利" value={money(a.largest_win)} accent="up" />
        <StatCard label="最大亏损" value={money(a.largest_loss)} accent="down" />
        <StatCard label="最长连胜" value={`${a.longest_win_streak}`} accent="up" />
        <StatCard label="最长连败" value={`${a.longest_loss_streak}`} accent="down" />
        <StatCard label="平均持仓" value={`${a.avg_holding_days.toFixed(1)}d`}
          sub={`盈 ${a.avg_winning_holding_days.toFixed(1)}d / 亏 ${a.avg_losing_holding_days.toFixed(1)}d`} />
        <StatCard label="每周交易" value={num2(a.avg_trades_per_week)} sub={`每月 ${num2(a.avg_trades_per_month)}`} />
        <StatCard label="毛利/毛损" value={money(a.gross_profit)} sub={money(a.gross_loss)} accent="up" />
        <StatCard label="当前连续" value={a.current_streak > 0 ? `+${a.current_streak}` : `${a.current_streak}`}
          accent={a.current_streak > 0 ? "up" : a.current_streak < 0 ? "down" : undefined} sub="末尾同号连续" />
      </div>

      {/* 2. 多空对比 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <DirectionCard title="多头" count={a.long_count} pctShare={a.long_pct}
          winRate={a.win_rate_long_pct} pnl={a.long_pnl} color="#3fb950" />
        <DirectionCard title="空头" count={a.short_count} pctShare={a.short_pct}
          winRate={a.win_rate_short_pct} pnl={a.short_pnl} color="#f85149" />
      </div>

      {/* 3. 扩展风险比率 */}
      {tagMetrics && (
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">扩展风险比率</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-4">
            {RATIO_GROUPS.map((g) => (
              <div key={g.title}>
                <p className="text-xs font-semibold text-[#58a6ff] mb-2">{g.title}</p>
                <dl className="space-y-1">
                  {g.rows.map((r) => (
                    <div key={r.key} className="flex items-center justify-between text-xs border-b border-[#21262d]/40 pb-1">
                      <dt className="text-[#8b949e] flex items-center gap-1">
                        {r.label}
                        <span className="text-[10px] text-[#3d444d] cursor-help" title={r.help}>ⓘ</span>
                      </dt>
                      <dd className="font-mono text-[#e6edf3]">{r.fmt(tagMetrics.risk_ratios[r.key])}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 4. 入场标签分组 */}
      {tagMetrics && tagMetrics.by_entry_tag.length > 0 && (
        <TagTable title="入场标签分组" keyLabel="入场标签" rows={tagMetrics.by_entry_tag} />
      )}

      {/* 5. 出场原因分组 */}
      {tagMetrics && tagMetrics.by_exit_reason.length > 0 && (
        <TagTable title="出场原因分组" keyLabel="出场原因" rows={tagMetrics.by_exit_reason} />
      )}

      {/* 6. 回合明细表 */}
      <div className="card">
        <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">
          回合明细 <span className="text-[#6e7681] font-normal text-xs">（共 {a.total_trades} 笔，显示前 100）</span>
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[#8b949e] border-b border-[#21262d]">
                <th className="text-left py-2 pr-3">入场→出场</th>
                <th className="text-left py-2 pr-3">方向</th>
                <th className="text-left py-2 pr-3">标签</th>
                <th className="text-left py-2 pr-3">出场原因</th>
                <th className="text-right py-2 pr-3">数量</th>
                <th className="text-right py-2 pr-3">入/出价</th>
                <th className="text-right py-2 pr-3">盈亏</th>
                <th className="text-right py-2 pr-3">盈亏%</th>
                <th className="text-right py-2">持仓天</th>
              </tr>
            </thead>
            <tbody>
              {a.round_trips.slice(0, 100).map((t) => (
                <tr key={t.trip_id} className="border-b border-[#21262d]/50 last:border-0 hover:bg-[#21262d]/30">
                  <td className="py-1.5 pr-3 font-mono text-[#8b949e]">
                    {t.entry_time.slice(0, 10)} → {t.exit_time.slice(0, 10)}
                  </td>
                  <td className={`py-1.5 pr-3 ${t.direction === "long" ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {t.direction === "long" ? "多" : "空"}
                  </td>
                  <td className="py-1.5 pr-3 text-[#8b949e]">{t.entry_tag}</td>
                  <td className="py-1.5 pr-3 text-[#8b949e]">{t.exit_reason}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">{t.qty}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">
                    {t.entry_price.toFixed(2)}/{t.exit_price.toFixed(2)}
                  </td>
                  <td className={`py-1.5 pr-3 text-right font-mono ${t.pnl >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)}
                  </td>
                  <td className={`py-1.5 pr-3 text-right font-mono ${t.pnl_pct >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
                  </td>
                  <td className="py-1.5 text-right font-mono text-[#8b949e]">{t.holding_days.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 7. 周期利润条 */}
      {periodic && (
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">周期利润分布</h3>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <PeriodBars title="按日" buckets={periodic.daily} />
            <PeriodBars title="按周" buckets={periodic.weekly} />
            <PeriodBars title="按月" buckets={periodic.monthly} />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-4">
            <PeriodCallout label="最佳单日" bucket={periodic.best_day} accent="up" />
            <PeriodCallout label="最差单日" bucket={periodic.worst_day} accent="down" />
            <PeriodCallout label="最佳单月" bucket={periodic.best_month} accent="up" />
            <PeriodCallout label="最差单月" bucket={periodic.worst_month} accent="down" />
          </div>
        </div>
      )}
    </div>
  )
}

// ── 多空卡片 ────────────────────────────────────────────────────
interface DirectionCardProps {
  title: string; count: number; pctShare: number
  winRate: number; pnl: number; color: string
}
function DirectionCard({ title, count, pctShare, winRate, pnl, color }: DirectionCardProps) {
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold" style={{ color }}>{title}</span>
        <span className="text-xs text-[#6e7681]">占比 {pctShare.toFixed(1)}%</span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <p className="text-[10px] text-[#6e7681]">笔数</p>
          <p className="font-mono text-sm text-[#e6edf3]">{count}</p>
        </div>
        <div>
          <p className="text-[10px] text-[#6e7681]">胜率</p>
          <p className="font-mono text-sm text-[#e6edf3]">{winRate.toFixed(1)}%</p>
        </div>
        <div>
          <p className="text-[10px] text-[#6e7681]">盈亏</p>
          <p className={`font-mono text-sm ${pnl >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
            {pnl >= 0 ? "+" : ""}{pnl.toFixed(0)}
          </p>
        </div>
      </div>
      <div className="mt-2 h-1.5 rounded-full bg-[#21262d] overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${Math.min(pctShare, 100)}%`, background: color }} />
      </div>
    </div>
  )
}

// ── 标签分组表 ──────────────────────────────────────────────────
function TagTable({ title, keyLabel, rows }: { title: string; keyLabel: string; rows: TagRow[] }) {
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">{title}</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-[#8b949e] border-b border-[#21262d]">
              <th className="text-left py-2 pr-3">{keyLabel}</th>
              <th className="text-right py-2 pr-3">笔数</th>
              <th className="text-right py-2 pr-3">胜率</th>
              <th className="text-right py-2 pr-3">利润</th>
              <th className="text-right py-2 pr-3">盈亏因子</th>
              <th className="text-right py-2 pr-3">均值</th>
              <th className="text-right py-2">均持</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isTotal = r.key === "TOTAL"
              return (
                <tr key={r.key} className={`border-b border-[#21262d]/50 last:border-0 ${
                  isTotal ? "bg-[#1f6feb]/5 font-semibold" : "hover:bg-[#21262d]/30"
                }`}>
                  <td className="py-1.5 pr-3 text-[#e6edf3]">{isTotal ? "合计" : r.key}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#8b949e]">{r.trades}</td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">{r.win_rate_pct.toFixed(1)}%</td>
                  <td className={`py-1.5 pr-3 text-right font-mono ${r.profit_abs >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {r.profit_abs >= 0 ? "+" : ""}{r.profit_abs.toFixed(0)}
                  </td>
                  <td className="py-1.5 pr-3 text-right font-mono text-[#e6edf3]">{r.profit_factor.toFixed(2)}</td>
                  <td className={`py-1.5 pr-3 text-right font-mono ${r.avg_pnl >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {r.avg_pnl.toFixed(1)}
                  </td>
                  <td className="py-1.5 text-right font-mono text-[#8b949e]">{r.avg_holding_days.toFixed(1)}d</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── 周期迷你条形图 ──────────────────────────────────────────────
function PeriodBars({ title, buckets }: { title: string; buckets: PeriodBucket[] }) {
  if (!buckets.length) {
    return (
      <div>
        <p className="text-xs text-[#6e7681] mb-2">{title}</p>
        <p className="text-[#6e7681] text-xs text-center py-8">无数据</p>
      </div>
    )
  }
  return (
    <div>
      <p className="text-xs text-[#6e7681] mb-2">{title}（{buckets.length} 桶）</p>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={buckets} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: "#8b949e", fontSize: 9 }} axisLine={false} tickLine={false}
            interval="preserveStartEnd" tickFormatter={(v: string) => v.slice(5)} />
          <YAxis tick={{ fill: "#8b949e", fontSize: 9 }} axisLine={false} tickLine={false} width={36} />
          <Tooltip
            contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }}
            formatter={(v: number) => [`$${v.toFixed(2)}`, "利润"]}
          />
          <Bar dataKey="profit_abs" radius={[2, 2, 0, 0]}>
            {buckets.map((b, i) => (
              <Cell key={i} fill={b.profit_abs >= 0 ? "#3fb950" : "#f85149"} fillOpacity={0.8} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function PeriodCallout({ label, bucket, accent }: { label: string; bucket: PeriodBucket | null; accent: "up" | "down" }) {
  return (
    <div className="bg-[#1c2128] rounded-lg py-2 px-3">
      <p className="text-[10px] text-[#6e7681] mb-1">{label}</p>
      {bucket ? (
        <>
          <p className={`font-mono text-sm font-bold ${accent === "up" ? "text-[#3fb950]" : "text-[#f85149]"}`}>
            {bucket.profit_abs >= 0 ? "+" : ""}${bucket.profit_abs.toFixed(0)}
          </p>
          <p className="text-[10px] text-[#6e7681]">{bucket.label}</p>
        </>
      ) : (
        <p className="text-[#6e7681] text-xs">—</p>
      )}
    </div>
  )
}
