import { useSharedConfig } from "./SharedConfig"

// 提示当前 Tab 使用的共享配置（V3 · H2）：让用户一眼确认「这次跑的是哪一组」。
export function SharedConfigNotice() {
  const { config } = useSharedConfig()
  return (
    <p className="text-[11px] text-[#6e7681] leading-relaxed bg-[#161b22] border border-[#21262d] rounded px-2.5 py-2">
      使用顶部共享配置：
      <span className="font-mono text-[#8b949e]">
        {" "}{config.strategy_name} · {config.symbol}({config.market}) · {config.frequency} ·{" "}
        {config.start_date}~{config.end_date}
      </span>
    </p>
  )
}
