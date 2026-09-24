import { formatStamp, type NewsSourceItem } from "@/hooks/useAiReports"

/**
 * 研报底部固定展示的「依据」与免责声明。
 *
 * `sources` 是**实际喂给模型的新闻**，不是「相关阅读」——
 * 一篇说不清依据的研报不如不生成，用户要能逐条点开核对模型有没有瞎编。
 * 所以它固定展示，不折叠、不做「查看更多」。
 */
interface ReportSourcesProps {
  sources: NewsSourceItem[]
  dataNotes: string[]
  disclaimer: string
  model: string
  generatedAt: string
}

export function ReportSources({
  sources, dataNotes, disclaimer, model, generatedAt,
}: ReportSourcesProps) {
  return (
    <div className="card space-y-4">
      <section>
        <h4 className="text-xs font-semibold text-[#e6edf3] mb-2">
          依据的新闻（{sources.length} 条）
        </h4>
        {sources.length > 0 ? (
          <ul className="space-y-1.5">
            {sources.map((item, index) => (
              <li key={`${item.title}-${index}`} className="text-[11px] leading-relaxed">
                <span className="font-mono text-[#6e7681] mr-2">
                  {formatStamp(item.published_at)}
                </span>
                {item.url ? (
                  <a href={item.url} target="_blank" rel="noreferrer"
                    className="text-[#58a6ff] hover:underline">
                    {item.title}
                  </a>
                ) : (
                  <span className="text-[#c9d1d9]">{item.title}</span>
                )}
                {item.publisher && (
                  <span className="text-[#6e7681] ml-2">· {item.publisher}</span>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[11px] text-[#d29922]">
            本期无可用新闻 —— 消息面一节不含任何模型自行补充的内容。
          </p>
        )}
      </section>

      {dataNotes.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold text-[#e6edf3] mb-2">数据缺口</h4>
          <ul className="space-y-1">
            {dataNotes.map((note, index) => (
              <li key={`${note}-${index}`} className="text-[11px] text-[#8b949e]">
                · {note}
              </li>
            ))}
          </ul>
        </section>
      )}

      <p className="text-[10px] text-[#6e7681] leading-relaxed border-t border-[#21262d] pt-3">
        {disclaimer}
      </p>
      <p className="text-[10px] text-[#6e7681] font-mono">
        模型 {model} · 生成于 {formatStamp(generatedAt)}
      </p>
    </div>
  )
}
