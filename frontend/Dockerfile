# ── 基础层 ────────────────────────────────────────────────────────
FROM node:22-alpine AS base
WORKDIR /app

# ── 开发镜像 ─────────────────────────────────────────────────────
FROM base AS development

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]

# ── 生产构建 ─────────────────────────────────────────────────────
FROM base AS builder

COPY package.json package-lock.json* ./
# 安装全部依赖（含 dev：vite、rollup 构建工具必须存在）
# rollup 在 Linux 上需要原生二进制，使用 --ignore-scripts=false 确保 postinstall 运行
RUN npm install --prefer-offline

COPY . .
RUN npm run build

# ── 生产镜像（Nginx 静态托管）────────────────────────────────────
FROM nginx:1.27-alpine AS production

# 移除默认配置
RUN rm /etc/nginx/conf.d/default.conf

# 复制自定义 Nginx 配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 复制 Vite 构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://localhost/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
