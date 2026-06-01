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
  },
})
