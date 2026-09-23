/**
 * WebSocket 客户端。
 *
 * ⚠️ 目前**没有任何调用方** —— 前端走的是轮询（行情 8s）。后端的
 * `/api/v1/stream/{bars,orders,portfolio,risk}` 四个端点同样没人消费。
 * 保留它是为了接线时有现成的重连逻辑，但接线前请先读下面这段。
 *
 * ## 地址必须同源推导，不能写死
 *
 * 早先这里是 `VITE_WS_URL ?? "ws://localhost:8000"`。那个默认值一旦被编译进
 * 生产镜像就废了：HTTPS 页面上 `ws://` 会被浏览器按混合内容拦掉，
 * 而远程访问时 `localhost` 指的是**用户自己的机器**、不是服务器。
 *
 * 现在按页面协议与主机推导（https → wss），把主机与端口交给反向代理 ——
 * 与 API 基址「空串 + 同源代理」的既有约定一致。
 *
 * 三层代理都必须透传 Upgrade/Connection，缺一层的表现都是
 * 「页面能开、行情不动」且不报错：
 *   - `frontend/vite.config.ts`（开发）
 *   - `frontend/nginx.conf`（前端容器）
 *   - `infra/nginx/conf.d/quantbot.conf`（生产边缘反代）
 */
function resolveWsBase(): string {
  const configured = import.meta.env.VITE_WS_URL
  if (configured) return configured

  // SSR / 单测环境没有 window，退回本地后端（仅开发期有意义）
  if (typeof window === "undefined") return "ws://localhost:8000"

  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${scheme}//${window.location.host}`
}

const WS_BASE = resolveWsBase()

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
