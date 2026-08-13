// 策略编辑器面板：Monaco 编辑自定义策略源码 + 服务端语法/接口校验。
import { useState } from "react"
import Editor from "@monaco-editor/react"
import { Spinner } from "@/components/ui/Spinner"
import { useToast } from "@/components/ui/Toast"
import { usePresets, useStrategySource, useValidateStrategy } from "@/hooks/useStrategy"

export function StrategyEditorPanel() {
  const { data: presets = [] } = usePresets()
  const { mutate: validate, isPending: validating, data: validResult } = useValidateStrategy()
  const { toast } = useToast()

  const [selectedPreset, setSelectedPreset] = useState<string>("blank")
  const [code, setCode] = useState<string>("")
  const [loadKey, setLoadKey] = useState<string>("blank")

  const { data: sourceData, isLoading: sourceLoading } = useStrategySource(loadKey)

  // When source is fetched, populate the editor
  if (sourceData && sourceData.source && sourceData.source !== code && loadKey) {
    setCode(sourceData.source)
  }

  function handleLoadTemplate() {
    setLoadKey(selectedPreset === loadKey ? selectedPreset + "_" : selectedPreset)
    setCode("")  // will be replaced when query resolves
  }

  function handleValidate() {
    if (!code.trim()) { toast("请先输入策略代码", "warning"); return }
    validate({ code })
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
      {/* Sidebar */}
      <div className="xl:col-span-1 space-y-4">
        {/* Template Picker */}
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">模板选择</h3>
          <div className="space-y-1.5 mb-3">
            <label className="label block mb-1">内置模板</label>
            <button
              onClick={() => setSelectedPreset("blank")}
              className={`w-full text-left px-3 py-1.5 rounded text-xs transition-colors ${
                selectedPreset === "blank"
                  ? "bg-[#1f6feb]/20 text-[#58a6ff]"
                  : "text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d]"
              }`}
            >
              📄 空白模板
            </button>
            {presets.map((p) => (
              <button
                key={p.name}
                onClick={() => setSelectedPreset(p.name)}
                className={`w-full text-left px-3 py-1.5 rounded text-xs transition-colors ${
                  selectedPreset === p.name
                    ? "bg-[#1f6feb]/20 text-[#58a6ff]"
                    : "text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d]"
                }`}
                title={p.description}
              >
                📊 {p.name}
              </button>
            ))}
          </div>
          <button
            className="btn btn-secondary w-full text-sm"
            onClick={handleLoadTemplate}
            disabled={sourceLoading}
          >
            {sourceLoading ? <Spinner size="sm" className="mx-auto" /> : "加载模板"}
          </button>
        </div>

        {/* Validate */}
        <div className="card">
          <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">代码验证</h3>
          <button
            className="btn btn-primary w-full"
            onClick={handleValidate}
            disabled={validating}
          >
            {validating ? <Spinner size="sm" className="mx-auto" /> : "✓ 验证代码"}
          </button>

          {validResult && (
            <div className="mt-3 space-y-2">
              {/* Status */}
              <div className={`flex items-center gap-2 text-sm font-medium ${
                validResult.valid ? "text-[#3fb950]" : "text-[#f85149]"
              }`}>
                <span>{validResult.valid ? "✓" : "✗"}</span>
                <span>{validResult.valid ? "验证通过" : "存在错误"}</span>
              </div>

              {/* Errors */}
              {validResult.errors.map((e, i) => (
                <div key={i} className="text-xs text-[#f85149] bg-[#2a1b1b] rounded px-2 py-1.5 leading-snug">
                  {e}
                </div>
              ))}

              {/* Warnings */}
              {validResult.warnings.map((w, i) => (
                <div key={i} className="text-xs text-[#e3b341] bg-[#2a2010] rounded px-2 py-1.5 leading-snug">
                  ⚠ {w}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Help */}
        <div className="card text-xs text-[#8b949e] space-y-1.5">
          <p className="font-semibold text-[#e6edf3]">API 速查</p>
          <p><code className="text-[#79c0ff]">ctx.bar</code> — 当前 K 线</p>
          <p><code className="text-[#79c0ff]">ctx.history</code> — DataFrame</p>
          <p><code className="text-[#79c0ff]">ctx.cash</code> — 可用资金</p>
          <p><code className="text-[#79c0ff]">ctx.qty</code> — 当前仓位</p>
          <p><code className="text-[#79c0ff]">ctx.buy(qty)</code> — 买入</p>
          <p><code className="text-[#79c0ff]">ctx.sell(qty)</code> — 卖出</p>
          <p><code className="text-[#79c0ff]">ctx.sell_all()</code> — 清仓</p>
        </div>
      </div>

      {/* Monaco Editor */}
      <div className="xl:col-span-3">
        <div className="card p-0 overflow-hidden" style={{ height: 560 }}>
          <div className="flex items-center justify-between px-4 py-2 bg-[#1c2128] border-b border-[#21262d]">
            <span className="text-xs text-[#8b949e] font-mono">strategy.py</span>
            <span className="text-xs text-[#6e7681]">Python · StrategyBase</span>
          </div>
          <Editor
            height="calc(100% - 37px)"
            defaultLanguage="python"
            value={code}
            onChange={(v) => setCode(v ?? "")}
            theme="vs-dark"
            options={{
              fontSize: 13,
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              minimap: { enabled: false },
              lineNumbers: "on",
              wordWrap: "on",
              scrollBeyondLastLine: false,
              padding: { top: 12, bottom: 12 },
              renderLineHighlight: "line",
              bracketPairColorization: { enabled: true },
            }}
          />
        </div>
        <p className="text-xs text-[#6e7681] mt-2 px-1">
          提示：编辑完成后点击「验证代码」检查语法，然后前往「回测」页面运行策略。
        </p>
      </div>
    </div>
  )
}
