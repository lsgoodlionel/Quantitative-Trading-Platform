import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import path from "path"

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3000,
    host: "0.0.0.0",
    proxy: {
      "/api": {
        // Docker 容器内用 backend 服务名；本地裸跑用 localhost
        target: process.env.BACKEND_URL ?? "http://localhost:8000",
        changeOrigin: true,
        // data-config/status 探测最长 10s，WS 超时关闭探测最长 30s
        timeout: 35000,
        proxyTimeout: 35000,
      },
      "/ws": {
        target: process.env.WS_URL ?? "ws://localhost:8000",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // 单元测试只认 src/ 下的用例。
    //
    // e2e/ 是 Playwright 的地盘：那里的 `test` 来自 @playwright/test，
    // 在 jsdom 里跑会炸出一堆和真实问题无关的报错（找不到 browser、fixture 不匹配），
    // 排查成本远大于收益。include 收窄 + exclude 明列，两道都设上，
    // 免得以后有人加了新的匹配规则又把 e2e 卷进来（V3 Wave D-b）。
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["**/node_modules/**", "**/dist/**", "e2e/**"],
  },
})
