// 二级 Tab 切换条（V3 · H2 合并 Tab 后复用）
interface SubTabsProps<T extends string> {
  tabs: { key: T; label: string }[]
  active: T
  onChange: (key: T) => void
}

export function SubTabs<T extends string>({ tabs, active, onChange }: SubTabsProps<T>) {
  return (
    <div className="flex gap-2 flex-wrap">
      {tabs.map(({ key, label }) => (
        <button key={key} type="button"
          aria-pressed={active === key}
          className={`px-3 py-1.5 text-xs rounded-md border transition-colors ${
            active === key
              ? "border-[#58a6ff] text-[#58a6ff] bg-[#58a6ff]/10"
              : "border-[#21262d] text-[#8b949e] hover:text-[#e6edf3]"
          }`}
          onClick={() => onChange(key)}>
          {label}
        </button>
      ))}
    </div>
  )
}
