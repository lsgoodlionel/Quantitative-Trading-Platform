import { Sidebar } from "./Sidebar"
import { TopBar } from "./TopBar"
import type { PageHelpData } from "@/data/pageHelp"

interface AppShellProps {
  title: string
  help?: PageHelpData
  children: React.ReactNode
}

export function AppShell({ title, help, children }: AppShellProps) {
  return (
    <div className="flex h-screen bg-[#0d1117] overflow-hidden">
      <Sidebar />
      {/* main area shifts right of sidebar */}
      <div className="flex flex-col flex-1 min-w-0 ml-16 lg:ml-52">
        <TopBar title={title} help={help} />
        <main className="flex-1 overflow-auto p-4 lg:p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
