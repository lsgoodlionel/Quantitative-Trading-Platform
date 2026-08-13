// 算法实验室 Tab（原 /algolab 整页）：六个量化模型 + 策略编辑器 + ML 策略。
// 由 pages/AlgoLab.tsx 迁入研究页的一个 Tab（V3 · H1），各面板内容未改。
import { useState } from "react"
import { Link } from "react-router-dom"
import { MLPanel } from "./algo/MLStrategyPanel"
import { BSMPanel } from "./algo/BSMPanel"
import { CointegrationPanel } from "./algo/CointegrationPanel"
import { GARCHPanel } from "./algo/GARCHPanel"
import { GBMPanel } from "./algo/GBMPanel"
import { HMMPanel } from "./algo/HMMPanel"
import { KellyPanel } from "./algo/KellyPanel"
import { StrategyEditorPanel } from "./algo/StrategyEditorPanel"

type AlgoPanel = "gbm" | "bsm" | "garch" | "kelly" | "coint" | "hmm" | "editor" | "ml"

const TABS: { id: AlgoPanel; label: string; cn: string }[] = [
  { id: "gbm",    label: "GBM 蒙卡",   cn: "几何布朗运动" },
  { id: "bsm",    label: "BSM 期权",   cn: "Black-Scholes-Merton" },
  { id: "garch",  label: "GARCH",      cn: "波动率建模" },
  { id: "kelly",  label: "凯利准则",    cn: "仓位优化" },
  { id: "coint",  label: "协整",        cn: "统计套利" },
  { id: "hmm",    label: "HMM 状态",   cn: "市场状态识别" },
  { id: "editor", label: "策略编辑器",  cn: "自定义策略" },
  { id: "ml",     label: "ML 策略",    cn: "机器学习预测" },
]

/** 每个模型的使用时机与下一步去向；nextPath 以 / 开头的是站内路由，否则是本页面板 id */
const USAGE: Record<AlgoPanel, { when: string; next: string; nextPath: string }> = {
  gbm:    { when: "估算标的未来价格分布、评估潜在收益和最坏情况", next: "→ 用波动率参数设置 GARCH 动态止损", nextPath: "" },
  bsm:    { when: "期权定价、Delta 对冲、Greeks 分析", next: "→ 对冲策略可在策略编辑器实现", nextPath: "editor" },
  garch:  { when: "预测近期波动率、设置动态止损位", next: "→ 将预测波动率用于风控 VaR 计算", nextPath: "/risk" },
  kelly:  { when: "根据策略历史表现确定每笔交易仓位比例", next: "→ 应用到风控最大持仓限制", nextPath: "/risk" },
  coint:  { when: "检验两支股票是否存在配对套利机会", next: "→ 通过后可用 pairs_trading 策略回测", nextPath: "/backtest?strategy=pairs_trading" },
  hmm:    { when: "识别市场所处的牛/熊/震荡状态，指导仓位方向", next: "→ 结合状态信号调整策略参数", nextPath: "/trading?tab=live" },
  editor: { when: "编写和验证自定义策略代码", next: "→ 保存后前往回测运行策略", nextPath: "/backtest" },
  ml:     { when: "训练机器学习模型预测价格方向", next: "→ 信号可集成到自定义策略中", nextPath: "editor" },
}

export function AlgoTab() {
  const [panel, setPanel] = useState<AlgoPanel>("gbm")
  const usage = USAGE[panel]

  return (
    <div>
      {/* 面板切换 */}
      <div className="flex flex-wrap gap-2 mb-6 border-b border-[#21262d] pb-4">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setPanel(t.id)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium border transition-colors ${
              panel === t.id
                ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/30"
                : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3]"
            }`}
          >
            {t.label}
            <span className="ml-1.5 text-xs text-[#6e7681] hidden sm:inline">· {t.cn}</span>
          </button>
        ))}
      </div>

      {/* 使用场景说明 */}
      <div className="flex items-start gap-3 px-4 py-2 bg-[#0d1117] border border-[#21262d] rounded-lg mb-4 text-xs text-[#6e7681]">
        <span className="text-[#58a6ff] shrink-0 mt-0.5">ℹ</span>
        <span><strong className="text-[#8b949e]">使用时机：</strong>{usage.when}</span>
        {usage.nextPath && (
          <span className="ml-auto shrink-0">
            {usage.nextPath.startsWith("/")
              ? <Link to={usage.nextPath} className="text-[#58a6ff] hover:underline">{usage.next}</Link>
              : <button onClick={() => setPanel(usage.nextPath as AlgoPanel)} className="text-[#58a6ff] hover:underline">{usage.next}</button>
            }
          </span>
        )}
      </div>

      {/* 面板 */}
      {panel === "gbm"    && <GBMPanel />}
      {panel === "bsm"    && <BSMPanel />}
      {panel === "garch"  && <GARCHPanel />}
      {panel === "kelly"  && <KellyPanel />}
      {panel === "coint"  && <CointegrationPanel />}
      {panel === "hmm"    && <HMMPanel />}
      {panel === "editor" && <StrategyEditorPanel />}
      {panel === "ml"     && <MLPanel />}
    </div>
  )
}
