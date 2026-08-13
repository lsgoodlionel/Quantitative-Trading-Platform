// 公司新闻面板（yfinance 新闻流；A 股暂无源）。
import { EmptyState } from "@/components/ui/EmptyState"
import { useCompanyNews, type CompanyNewsItem } from "@/hooks/useMarketEvents"
import type { Market } from "@/types"
import { LoadingBlock, Warnings, fmtDateTime } from "./shared"

function NewsCard({ item }: { item: CompanyNewsItem }) {
  const body = (
    <div className="flex gap-3 rounded-lg border border-[#21262d] bg-[#161b22] p-3 transition-colors hover:border-[#30363d]">
      {item.thumbnail && (
        <img
          src={item.thumbnail}
          alt=""
          loading="lazy"
          className="h-16 w-16 shrink-0 rounded object-cover"
        />
      )}
      <div className="min-w-0 flex-1">
        <div className="line-clamp-2 text-sm font-medium text-[#e6edf3]">{item.title}</div>
        {item.summary && (
          <div className="mt-1 line-clamp-2 text-xs text-[#8b949e]">{item.summary}</div>
        )}
        <div className="mt-1.5 flex items-center gap-2 text-[11px] text-[#484f58]">
          {item.publisher && <span>{item.publisher}</span>}
          <span>{fmtDateTime(item.published_at)}</span>
        </div>
      </div>
    </div>
  )
  return item.url ? (
    <a href={item.url} target="_blank" rel="noopener noreferrer" className="block">
      {body}
    </a>
  ) : (
    body
  )
}

export function NewsPanel({ symbol, market }: { symbol: string; market: Market }) {
  const { data, isLoading, isError } = useCompanyNews(symbol, market)
  if (isLoading) return <LoadingBlock />
  if (isError) return <EmptyState title="新闻加载失败" description="数据源暂不可用，请稍后重试" />
  return (
    <div>
      <Warnings items={data?.warnings ?? []} />
      {data && data.items.length > 0 ? (
        <div className="space-y-2">
          {data.items.map((item, i) => (
            <NewsCard key={item.url ?? i} item={item} />
          ))}
        </div>
      ) : (
        <EmptyState title="暂无新闻" description="该标的近期无新闻或数据源不覆盖" />
      )}
    </div>
  )
}
