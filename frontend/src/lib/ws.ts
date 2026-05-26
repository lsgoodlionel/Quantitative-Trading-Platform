const WS_BASE = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000"

type MessageHandler<T> = (data: T) => void

export class WebSocketClient<T = unknown> {
  private ws: WebSocket | null = null
  private handlers: Set<MessageHandler<T>> = new Set()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectAttempts = 0
  private readonly maxReconnectAttempts = 10

  constructor(private readonly path: string) {}

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return

    this.ws = new WebSocket(`${WS_BASE}${this.path}`)

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data as string) as T
        if ((data as { type?: string }).type === "ping") return
        this.handlers.forEach((h) => h(data))
      } catch {
        // ignore parse errors
      }
    }

    this.ws.onclose = () => {
      this.scheduleReconnect()
    }

    this.ws.onerror = () => {
      this.ws?.close()
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0
    }
  }

  subscribe(handler: MessageHandler<T>): () => void {
    this.handlers.add(handler)
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
      this.connect()
    }
    return () => this.handlers.delete(handler)
  }

  disconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this.ws = null
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30_000)
    this.reconnectAttempts++
    this.reconnectTimer = setTimeout(() => this.connect(), delay)
  }
}

// 单例连接（按频道复用）
const clients = new Map<string, WebSocketClient>()

export function getWsClient<T>(path: string): WebSocketClient<T> {
  if (!clients.has(path)) {
    clients.set(path, new WebSocketClient<T>(path) as unknown as WebSocketClient<unknown>)
  }
  return clients.get(path) as WebSocketClient<T>
}
