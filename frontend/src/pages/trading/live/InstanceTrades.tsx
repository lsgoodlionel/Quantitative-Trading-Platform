// 实例卡「成交记录」页：模拟期间的每笔买卖与合计实现盈亏。
import type { PaperSimResult } from "@/types"

export function InstanceTrades({ paper }: { paper: PaperSimResult | null }) {
  return (
          <div>
            {!paper || paper.trades.length === 0 ? (
              <div className="text-center py-8 text-[#6e7681] text-xs">
                <p>模拟期间无交易记录</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-[#6e7681] border-b border-[#21262d]">
                      <th className="text-left pb-2 pr-3">时间</th>
                      <th className="text-left pb-2 pr-3">方向</th>
                      <th className="text-right pb-2 pr-3">价格</th>
                      <th className="text-right pb-2 pr-3">数量</th>
                      <th className="text-right pb-2 pr-3">金额</th>
                      <th className="text-right pb-2">实现盈亏</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#21262d]">
                    {paper.trades.map((t, i) => (
                      <tr key={i} className="hover:bg-[#161b22] transition-colors">
                        <td className="py-2 pr-3 text-[#8b949e] font-mono">{t.timestamp}</td>
                        <td className="py-2 pr-3">
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            t.side === "BUY" ? "bg-[#3fb950]/15 text-[#3fb950]" : "bg-[#f85149]/15 text-[#f85149]"
                          }`}>
                            {t.side === "BUY" ? "买入" : "卖出"}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">${t.price.toFixed(2)}</td>
                        <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{t.qty}</td>
                        <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">${t.value.toLocaleString()}</td>
                        <td className={`py-2 text-right font-mono font-bold ${
                          t.realized_pnl > 0 ? "text-[#3fb950]" : t.realized_pnl < 0 ? "text-[#f85149]" : "text-[#8b949e]"
                        }`}>
                          {t.side === "BUY" ? "—" : `${t.realized_pnl >= 0 ? "+" : ""}$${t.realized_pnl.toFixed(2)}`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  {paper.trades.length > 0 && (
                    <tfoot>
                      <tr className="border-t border-[#30363d]">
                        <td colSpan={5} className="pt-2 text-[#6e7681]">合计实现盈亏</td>
                        <td className={`pt-2 text-right font-mono font-bold ${
                          paper.trades.reduce((s, t) => s + t.realized_pnl, 0) >= 0 ? "text-[#3fb950]" : "text-[#f85149]"
                        }`}>
                          {(() => {
                            const total = paper.trades.reduce((s, t) => s + t.realized_pnl, 0)
                            return `${total >= 0 ? "+" : ""}$${total.toFixed(2)}`
                          })()}
                        </td>
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
            )}
          </div>
  )
}
