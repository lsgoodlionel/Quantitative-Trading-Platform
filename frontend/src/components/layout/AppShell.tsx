import { Sidebar } from "./Sidebar"
import { TopBar } from "./TopBar"

interface AppShellProps {
  title: string
  children: React.ReactNode
}

export function AppShell({ title, children }: AppShellProps) {
  return (
    <div className="flex h-screen bg-[#0d1117] overflow-hidden">
      <Sidebar />
      {/* main area shifts right of sidebar */}
      <div className="flex flex-col flex-1 min-w-0 ml-16 lg:ml-52">
        <TopBar title={title} />
        <main className="flex-1 overflow-auto p-4 lg:p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
