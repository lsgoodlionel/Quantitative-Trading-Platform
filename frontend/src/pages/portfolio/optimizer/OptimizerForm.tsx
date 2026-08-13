// 组合优化配置表单：标的池、区间、优化方法与各方法专属参数。
// 状态由 OptimizerTab 持有（结果区要用同一份 market），这里只负责渲染与回调。
import type { Dispatch, SetStateAction } from "react"
import { Spinner } from "@/components/ui/Spinner"
import type { BLViewInput, HrpLinkage } from "@/hooks/usePortfolioAdvanced"
import type { Market } from "@/types"
import { BLViewsEditor } from "./BLViewsEditor"
import {
  HRP_LINKAGE_OPTIONS, METHOD_OPTIONS, RETURNS_OPTIONS, RISK_MODEL_OPTIONS,
  isBlackLitterman, isHrp, isTailRisk, yearsAgo,
  type ExpectedReturnsMethod, type PortfolioOptMethod, type RiskModel,
} from "./options"

export interface FormState {
  symbolsText: string
  market: Market
  start_date: string
  end_date: string
  method: PortfolioOptMethod
  include_frontier: boolean
  risk_model: RiskModel
  expected_returns_method: ExpectedReturnsMethod
  views: BLViewInput[]
  linkage_method: HrpLinkage
  cvar_beta: number
}

interface OptimizerFormProps {
  form: FormState
  setForm: Dispatch<SetStateAction<FormState>>
  onMarketChange: (market: string) => void
  onSubmit: (e: React.FormEvent) => void
  isPending: boolean
  /** 当前已输入的标的，供 BL 观点编辑器做下拉选项 */
  symbols: string[]
}

export function OptimizerForm({
  form, setForm, onMarketChange, onSubmit, isPending, symbols,
}: OptimizerFormProps) {
  return (
        <form onSubmit={onSubmit} className="xl:col-span-1 card h-fit space-y-4">
          <h2 className="text-sm font-semibold text-[#e6edf3]">优化配置</h2>

          {/* 市场 */}
          <div>
            <label className="label">市场</label>
            <div className="flex gap-1 mt-1">
              {(["US", "HK", "A"] as Market[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => onMarketChange(m)}
                  className={`flex-1 py-1.5 rounded text-xs font-medium border transition-colors ${
                    form.market === m
                      ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/40"
                      : "text-[#6e7681] border-[#30363d] hover:text-[#e6edf3]"
                  }`}
                >
                  {m === "A" ? "A股" : m}
                </button>
              ))}
            </div>
          </div>

          {/* 标的列表 */}
          <div>
            <label className="label">
              标的列表
              <span className="ml-1 text-[#6e7681] text-[10px]">逗号或换行分隔</span>
            </label>
            <textarea
              className="input w-full mt-1 font-mono text-xs resize-none"
              rows={5}
              value={form.symbolsText}
              onChange={(e) => setForm((f) => ({ ...f, symbolsText: e.target.value }))}
              placeholder="AAPL, MSFT, GOOGL"
            />
          </div>

          {/* 日期区间 */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">开始</label>
              <input className="input w-full mt-1" type="date" value={form.start_date}
                onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))} />
            </div>
            <div>
              <label className="label">结束</label>
              <input className="input w-full mt-1" type="date" value={form.end_date}
                onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))} />
            </div>
          </div>

          {/* 快捷日期 */}
          <div className="flex gap-1">
            {[1, 2, 3, 5].map((y) => (
              <button
                key={y}
                type="button"
                onClick={() => setForm((f) => ({ ...f, start_date: yearsAgo(y) }))}
                className="flex-1 text-xs py-1 rounded border border-[#30363d] text-[#6e7681] hover:text-[#e6edf3] hover:border-[#58a6ff]/40 transition-colors"
              >
                {y}年
              </button>
            ))}
          </div>

          {/* 优化方法 */}
          <div>
            <label className="label">优化方法</label>
            <div className="space-y-1.5 mt-1">
              {METHOD_OPTIONS.map((m) => (
                <label
                  key={m.value}
                  className={`flex items-start gap-2 p-2 rounded cursor-pointer border transition-colors ${
                    form.method === m.value
                      ? "border-[#58a6ff]/40 bg-[#1f6feb]/10"
                      : "border-[#30363d] hover:border-[#58a6ff]/20"
                  }`}
                >
                  <input
                    type="radio"
                    name="method"
                    value={m.value}
                    checked={form.method === m.value}
                    onChange={() => setForm((f) => ({ ...f, method: m.value }))}
                    className="mt-0.5 accent-[#58a6ff]"
                  />
                  <div>
                    <p className="text-xs font-medium text-[#e6edf3]">{m.label}</p>
                    <p className="text-[10px] text-[#6e7681]">{m.desc}</p>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* Black-Litterman 观点输入 */}
          {isBlackLitterman(form.method) && (
            <BLViewsEditor
              views={form.views}
              symbols={symbols}
              onChange={(views) => setForm((f) => ({ ...f, views }))}
            />
          )}

          {/* HRP 聚类连接方式 */}
          {isHrp(form.method) && (
            <div>
              <label className="label">聚类连接方式（HRP）</label>
              <select
                className="input w-full mt-1 text-xs"
                value={form.linkage_method}
                onChange={(e) => setForm((f) => ({ ...f, linkage_method: e.target.value as HrpLinkage }))}
              >
                {HRP_LINKAGE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          )}

          {/* CVaR/CDaR 置信水平 */}
          {isTailRisk(form.method) && (
            <div>
              <label className="label">
                尾部置信水平 β
                <span className="ml-1 text-[#58a6ff] font-mono">{(form.cvar_beta * 100).toFixed(0)}%</span>
              </label>
              <input
                type="range" min={0.8} max={0.99} step={0.01}
                className="w-full mt-2 accent-[#58a6ff]"
                value={form.cvar_beta}
                onChange={(e) => setForm((f) => ({ ...f, cvar_beta: Number(e.target.value) }))}
              />
              <p className="text-[10px] text-[#6e7681] mt-1">
                关注最差 {((1 - form.cvar_beta) * 100).toFixed(0)}% 情景的{form.method === "min_cdar" ? "回撤" : "损失"}
              </p>
            </div>
          )}

          {/* 风险模型 */}
          <div>
            <label className="label">风险模型（协方差估计）</label>
            <select
              className="input w-full mt-1 text-xs"
              value={form.risk_model}
              onChange={(e) => setForm((f) => ({ ...f, risk_model: e.target.value as RiskModel }))}
            >
              {RISK_MODEL_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <p className="text-[10px] text-[#6e7681] mt-1">
              {RISK_MODEL_OPTIONS.find((o) => o.value === form.risk_model)?.desc}
            </p>
          </div>

          {/* 预期收益估计 */}
          <div>
            <label className="label">预期收益估计</label>
            <select
              className="input w-full mt-1 text-xs"
              value={form.expected_returns_method}
              onChange={(e) => setForm((f) => ({ ...f, expected_returns_method: e.target.value as ExpectedReturnsMethod }))}
            >
              {RETURNS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <p className="text-[10px] text-[#6e7681] mt-1">
              {RETURNS_OPTIONS.find((o) => o.value === form.expected_returns_method)?.desc}
            </p>
          </div>

          {/* 有效前沿开关 */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={form.include_frontier}
              onChange={(e) => setForm((f) => ({ ...f, include_frontier: e.target.checked }))}
              className="accent-[#58a6ff]"
            />
            <span className="text-xs text-[#8b949e]">计算有效前沿（较慢）</span>
          </label>

          <button
            type="submit"
            disabled={isPending}
            className="btn btn-primary w-full"
          >
            {isPending ? <Spinner size="sm" className="mx-auto" /> : "▶ 开始优化"}
          </button>
        </form>
  )
}
