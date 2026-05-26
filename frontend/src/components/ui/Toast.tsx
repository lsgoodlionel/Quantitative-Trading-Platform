import { createContext, useCallback, useContext, useState } from "react"
import { clsx } from "clsx"

type ToastVariant = "success" | "error" | "warning" | "info"

interface Toast {
  id: number
  message: string
  variant: ToastVariant
}

interface ToastContextValue {
  toast: (message: string, variant?: ToastVariant) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

let nextId = 0

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const toast = useCallback((message: string, variant: ToastVariant = "info") => {
    const id = ++nextId
    setToasts((prev) => [...prev, { id, message, variant }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 4000)
  }, [])

  const dismiss = (id: number) => setToasts((prev) => prev.filter((t) => t.id !== id))

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 w-80">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={clsx(
              "flex items-start gap-3 px-4 py-3 rounded-lg border text-sm shadow-lg",
              t.variant === "success" && "bg-[#162a1e] border-[#3fb950]/40 text-[#3fb950]",
              t.variant === "error" && "bg-[#2a1b1b] border-[#f85149]/40 text-[#f85149]",
              t.variant === "warning" && "bg-[#272111] border-[#e3b341]/40 text-[#e3b341]",
              t.variant === "info" && "bg-[#1c2536] border-[#58a6ff]/40 text-[#58a6ff]",
            )}
          >
            <span className="flex-1">{t.message}</span>
            <button onClick={() => dismiss(t.id)} className="opacity-60 hover:opacity-100 text-xs">✕</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error("useToast must be used within ToastProvider")
  return ctx
}
