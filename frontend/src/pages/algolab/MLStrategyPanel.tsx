import { useState } from "react"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import {
  AreaChart, Area, BarChart, Bar as RBar, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts"
import { useMLTrain, type MLModelType } from "@/hooks/useMLStrategy"
import { SectionCard, ParamRow, MetaGrid } from "./shared"

// ── Constants ─────────────────────────────────────────────────────

const MODEL_OPTIONS: { value: MLModelType; label: string; desc: string }[] = [
  { value: "random_forest",       label: "随机森林", desc: "集成树模型，抗过拟合，特征重要度清晰" },
  { value: "gradient_boosting",   label: "梯度提升", desc: "XGBoost 风格，精度高，训练较慢" },
  { value: "logistic_regression", label: "逻辑回归", desc: "线性基线，可解释性最强" },
]

const SIGNAL_CFG = {
  BUY:     { color: "#3fb950", bg: "bg-[#162a1e] border-[#3fb950]/40", label: "买入信号" },
  SELL:    { color: "#f85149", bg: "bg-[#2a1b1b] border-[#f85149]/40", label: "卖出信号" },
  NEUTRAL: { color: "#8b949e", bg: "bg-[#1c2128] border-[#30363d]",    label: "中性信号" },
}

// ── MLPanel ───────────────────────────────────────────────────────

export function MLPanel() {
  const { mutate: train, isPending, data: result, error } = useMLTrain()
  const { toast } = useToast()

  const [symbol,    setSymbol]    = useState("AAPL")
  const [market,    setMarket]    = useState("US")
  const [modelType, setModelType] = useState<MLModelType>("random_forest")
  const [fwdDays,   setFwdDays]   = useState("5")
  const [testSize,  setTestSize]  = useState("0.2")

  function run() {
    if (!symbol.trim()) { toast("请输入标的代码", "warning"); return }
    train({
      symbol:       symbol.trim().toUpperCase(),
      market,
      frequency:    "1d",
      model_type:   modelType,
      forward_days: parseInt(fwdDays) || 5,
      test_size:    parseFloat(testSize) || 0.2,
    })
  }

  const signalCfg = result ? SIGNAL_CFG[result.recent_signal] : null
  const fiData    = result?.feature_importance ?? []

  const cm = result?.confusion_matrix
  const tn = cm?.[0]?.[0] ?? 0
  const fp = cm?.[0]?.[1] ?? 0
  const fn = cm?.[1]?.[0] ?? 0
  const tp = cm?.[1]?.[1] ?? 0

  const predData = (result?.predictions.slice(-20) ?? []).map((p, i) => ({
    t:    i,
    prob: +(p.probability * 100).toFixed(1),
  }))

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      {/* Config sidebar */}
      <div className="xl:col-span-1 space-y-4">
        <SectionCard title="模型配置" sub="ML 分类策略">
          <ParamRow label="标的代码">
            <input className="input w-28 font-mono uppercase" value={symbol}
              onChange={e => setSymbol(e.target.value)} placeholder="AAPL" />
          </ParamRow>
          <ParamRow label="市场">
            <div className="flex gap-1">
              {["US", "HK", "A"].map(m => (
                <button key={m} onClick={() => setMarket(m)}
                  className={`px-3 py-1 rounded text-xs font-medium border transition-colors ${
                    market === m
                      ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/40"
                      : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3]"
                  }`}>{m}</button>
              ))}
            </div>
          </ParamRow>

          <div className="mb-3">
            <label className="label block mb-2">模型类型</label>
            <div className="space-y-1.5">
              {MODEL_OPTIONS.map(opt => (
                <label key={opt.value}
                  className={`flex items-start gap-2.5 p-2 rounded cursor-pointer border transition-colors ${
                    modelType === opt.value
                      ? "bg-[#1f6feb]/10 border-[#58a6ff]/30"
                      : "border-transparent hover:bg-[#21262d]"
                  }`}>
                  <input type="radio" name="ml-model" value={opt.value}
                    checked={modelType === opt.value}
                    onChange={() => setModelType(opt.value)}
                    className="mt-0.5 accent-[#58a6ff]" />
                  <div>
                    <p className="text-xs text-[#e6edf3] font-medium">{opt.label}</p>
                    <p className="text-[10px] text-[#6e7681] leading-snug">{opt.desc}</p>
                  </div>
                </label>
              ))}
            </div>
          </div>

          <ParamRow label="前瞻期（天）">
            <select className="select" value={fwdDays} onChange={e => setFwdDays(e.target.value)}>
              {["1", "3", "5", "10", "20"].map(v => <option key={v} value={v}>{v}天</option>)}
            </select>
          </ParamRow>
          <ParamRow label="测试集比例">
            <select className="select" value={testSize} onChange={e => setTestSize(e.target.value)}>
              <option value="0.1">10%</option>
              <option value="0.2">20%</option>
              <option value="0.3">30%</option>
            </select>
          </ParamRow>

          <button className="btn btn-primary w-full mt-2" onClick={run} disabled={isPending}>
            {isPending ? <Spinner size="sm" className="mx-auto" /> : "训练模型"}
          </button>
          {error && <p className="text-[#f85149] text-xs mt-2">{error.message}</p>}
        </SectionCard>

        {result && signalCfg && (
          <div className={`card border rounded-xl text-center ${signalCfg.bg}`}>
            <p className="text-xs text-[#8b949e] mb-1">最新信号</p>
            <p className="text-2xl font-bold mb-1" style={{ color: signalCfg.color }}>
              {signalCfg.label}
            </p>
            <p className="font-mono text-sm" style={{ color: signalCfg.color }}>
              {(result.recent_prob * 100).toFixed(1)}% 概率
            </p>
            <p className="text-[10px] text-[#6e7681] mt-2">
              {result.symbol} · 前瞻{result.forward_days}天
            </p>
          </div>
        )}
      </div>

      {/* Results area */}
      <div className="xl:col-span-3 space-y-4">
        {isPending && (
          <div className="card flex flex-col items-center justify-center h-48 gap-3 text-[#6e7681]">
            <Spinner size="lg" />
            <p className="text-sm">正在训练模型，请稍候...</p>
          </div>
        )}

        {result && !isPending && (<>
          <MetaGrid items={[
            { label: "训练集准确率", value: `${(result.train_accuracy * 100).toFixed(1)}%`, accent: result.train_accuracy > 0.6 ? "up" : "down" },
            { label: "测试集准确率", value: `${(result.test_accuracy  * 100).toFixed(1)}%`, accent: result.test_accuracy  > 0.55 ? "up" : "down" },
            { label: "精确率",       value: `${(result.precision * 100).toFixed(1)}%` },
            { label: "召回率",       value: `${(result.recall    * 100).toFixed(1)}%` },
            { label: "F1 Score",     value: result.f1_score.toFixed(4) },
            { label: "AUC-ROC",      value: result.auc_roc.toFixed(4), accent: result.auc_roc > 0.55 ? "up" : "down" },
            { label: "CV 均值",      value: `${(result.cv_mean * 100).toFixed(1)}%` },
            { label: "CV 标准差",    value: `±${(result.cv_std * 100).toFixed(1)}%` },
          ]} />

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <SectionCard title="特征重要度" sub="降序排列">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={fiData} layout="vertical"
                  margin={{ top: 4, right: 16, left: 60, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d" horizontal={false} />
                  <XAxis type="number" tick={{ fill: "#8b949e", fontSize: 10 }}
                    tickFormatter={v => v.toFixed(3)} />
                  <YAxis type="category" dataKey="name" width={60}
                    tick={{ fill: "#8b949e", fontSize: 10 }} />
                  <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 12 }}
                    formatter={(v: number) => [v.toFixed(6), "重要度"]} />
                  <RBar dataKey="importance" radius={[0, 3, 3, 0]}>
                    {fiData.map((_, i) => (
                      <Cell key={i} fill={i === 0 ? "#3fb950" : i < 3 ? "#58a6ff" : "#6e7681"} />
                    ))}
                  </RBar>
                </BarChart>
              </ResponsiveContainer>
            </SectionCard>

            <SectionCard title="混淆矩阵" sub="测试集">
              <div className="flex flex-col gap-2 mt-2">
                <div className="grid grid-cols-3 gap-1 text-xs text-center">
                  <div />
                  <div className="text-[#8b949e] py-1">预测: 上涨</div>
                  <div className="text-[#8b949e] py-1">预测: 下跌</div>

                  <div className="text-[#8b949e] flex items-center justify-end pr-2">实际: 上涨</div>
                  <div className="bg-[#162a1e] border border-[#3fb950]/30 rounded-lg p-3 text-center">
                    <p className="text-[#3fb950] font-mono font-bold text-lg">{tp}</p>
                    <p className="text-[10px] text-[#6e7681]">TP 真正</p>
                  </div>
                  <div className="bg-[#2a1b1b] border border-[#f85149]/30 rounded-lg p-3 text-center">
                    <p className="text-[#f85149] font-mono font-bold text-lg">{fn}</p>
                    <p className="text-[10px] text-[#6e7681]">FN 漏报</p>
                  </div>

                  <div className="text-[#8b949e] flex items-center justify-end pr-2">实际: 下跌</div>
                  <div className="bg-[#2a1b1b] border border-[#f85149]/30 rounded-lg p-3 text-center">
                    <p className="text-[#f85149] font-mono font-bold text-lg">{fp}</p>
                    <p className="text-[10px] text-[#6e7681]">FP 误报</p>
                  </div>
                  <div className="bg-[#162a1e] border border-[#3fb950]/30 rounded-lg p-3 text-center">
                    <p className="text-[#3fb950] font-mono font-bold text-lg">{tn}</p>
                    <p className="text-[10px] text-[#6e7681]">TN 真负</p>
                  </div>
                </div>
                <p className="text-[10px] text-[#6e7681] text-center mt-1">
                  总样本: {tp + tn + fp + fn} · 正确: {tp + tn} ·{" "}
                  准确率: {(((tp + tn) / Math.max(tp + tn + fp + fn, 1)) * 100).toFixed(1)}%
                </p>
              </div>
            </SectionCard>
          </div>

          <SectionCard title="近期预测概率" sub="最近20根 K 线，蓝线为买入概率">
            <ResponsiveContainer width="100%" height={160}>
              <AreaChart data={predData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="ml-prob-fill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#58a6ff" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#58a6ff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
                <XAxis dataKey="t" tick={{ fill: "#8b949e", fontSize: 10 }} />
                <YAxis domain={[0, 100]} tick={{ fill: "#8b949e", fontSize: 10 }} width={36}
                  tickFormatter={v => `${v}%`} />
                <ReferenceLine y={60} stroke="#3fb950" strokeDasharray="4 4"
                  label={{ value: "买入阈值60%", fill: "#3fb950", fontSize: 10, position: "right" }} />
                <ReferenceLine y={40} stroke="#f85149" strokeDasharray="4 4"
                  label={{ value: "卖出阈值40%", fill: "#f85149", fontSize: 10, position: "right" }} />
                <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 12 }}
                  formatter={(v: number) => [`${v}%`, "上涨概率"]} />
                <Area dataKey="prob" stroke="#58a6ff" strokeWidth={2}
                  fill="url(#ml-prob-fill)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </SectionCard>

          <p className="text-xs text-[#6e7681] px-1">
            样本数: {result.n_samples} · 特征数: {result.n_features} · 时间序列交叉验证 5 折 · 前瞻期: {result.forward_days}天
          </p>
        </>)}

        {!result && !isPending && (
          <div className="card flex items-center justify-center h-48 text-[#6e7681] text-sm">
            选择标的和模型后点击"训练模型"
          </div>
        )}
      </div>
    </div>
  )
}
