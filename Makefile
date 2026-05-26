# ============================================================
# QuantBot — 一键操作命令集
# 用法: make <target>
# ============================================================

.PHONY: help dev prod down logs ps test test-backend test-frontend \
        build-prod clean reset shell-backend shell-db seed-check

COMPOSE_DEV  := docker compose -f infra/docker-compose.yml
COMPOSE_PROD := docker compose -f infra/docker-compose.prod.yml
BACKEND_VENV := backend/.venv/bin

# ── 颜色输出 ─────────────────────────────────────────────────
BOLD  := \033[1m
GREEN := \033[0;32m
CYAN  := \033[0;36m
RESET := \033[0m

## help: 显示帮助信息
help:
	@echo ""
	@echo "$(BOLD)QuantBot 操作命令$(RESET)"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@grep -E '^## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=": "}{printf "  $(CYAN)%-22s$(RESET) %s\n", $$1, $$2}' | sed 's/## //'
	@echo ""

# ── 环境初始化 ────────────────────────────────────────────────

## setup: 初始化项目（首次运行）
setup:
	@echo "$(GREEN)▶ 检查 .env 文件...$(RESET)"
	@[ -f .env ] || (cp .env.example .env && echo "  已创建 .env，请编辑填写密钥后重新运行")
	@[ -f .env ] && echo "  .env 已存在，跳过"
	@echo "$(GREEN)▶ 检查 Docker...$(RESET)"
	@docker info > /dev/null 2>&1 || (echo "  错误: Docker 未运行" && exit 1)
	@echo "$(GREEN)✓ 环境就绪，运行 make dev 启动开发环境$(RESET)"

# ── 开发环境 ──────────────────────────────────────────────────

## dev: 启动完整开发环境（热重载）
dev: setup
	@echo "$(GREEN)▶ 启动开发环境...$(RESET)"
	$(COMPOSE_DEV) up --build -d
	@echo ""
	@echo "$(BOLD)服务地址:$(RESET)"
	@echo "  前端:    http://localhost:3000"
	@echo "  后端API: http://localhost:8000/docs"
	@echo "  数据库:  localhost:5432"
	@echo ""
	@echo "  默认账号: admin / admin123"

## dev-monitor: 启动开发环境 + 监控
dev-monitor: setup
	$(COMPOSE_DEV) --profile monitoring up --build -d

## dev-logs: 实时查看开发环境日志
dev-logs:
	$(COMPOSE_DEV) logs -f --tail=100

# ── 生产环境 ──────────────────────────────────────────────────

## prod: 构建并启动生产环境
prod: setup
	@echo "$(GREEN)▶ 构建生产镜像...$(RESET)"
	$(COMPOSE_PROD) build --no-cache
	@echo "$(GREEN)▶ 启动生产环境...$(RESET)"
	$(COMPOSE_PROD) up -d
	@echo ""
	@echo "$(BOLD)生产服务地址:$(RESET)"
	@echo "  前端+API: http://localhost:80"
	@echo "  (API via Nginx proxy: /api/, /ws/)"
	@echo ""
	@make prod-health

## prod-monitor: 生产环境 + 监控
prod-monitor:
	$(COMPOSE_PROD) --profile monitoring up -d

## prod-health: 检查生产服务健康状态
prod-health:
	@echo "$(GREEN)▶ 健康检查...$(RESET)"
	@sleep 3
	@curl -sf http://localhost/health > /dev/null && \
		echo "  前端 Nginx: $(GREEN)✓ OK$(RESET)" || \
		echo "  前端 Nginx: 未就绪"
	@curl -sf http://localhost/api/v1/health > /dev/null 2>&1 && \
		echo "  后端 API:   $(GREEN)✓ OK$(RESET)" || \
		echo "  后端 API:   未就绪（可能还在启动）"

## prod-logs: 查看生产日志
prod-logs:
	$(COMPOSE_PROD) logs -f --tail=100

# ── 停止 & 清理 ───────────────────────────────────────────────

## down: 停止所有服务（保留数据卷）
down:
	$(COMPOSE_DEV) down --remove-orphans 2>/dev/null || true
	$(COMPOSE_PROD) down --remove-orphans 2>/dev/null || true

## reset: 停止并删除所有数据（危险！）
reset:
	@read -p "将删除所有数据卷，确认? [y/N] " confirm && [ "$$confirm" = "y" ]
	$(COMPOSE_DEV) down -v --remove-orphans 2>/dev/null || true
	$(COMPOSE_PROD) down -v --remove-orphans 2>/dev/null || true
	@echo "$(GREEN)✓ 已清理$(RESET)"

## clean: 清理构建缓存和悬挂镜像
clean:
	docker image prune -f
	docker builder prune -f

# ── 服务状态 ──────────────────────────────────────────────────

## ps: 查看运行中的容器
ps:
	@$(COMPOSE_DEV) ps 2>/dev/null; $(COMPOSE_PROD) ps 2>/dev/null

## logs: 查看日志（默认后端）
logs:
	$(COMPOSE_DEV) logs -f backend 2>/dev/null || $(COMPOSE_PROD) logs -f backend

# ── 测试 ──────────────────────────────────────────────────────

## test: 运行全部测试
test: test-backend test-frontend

## test-backend: 运行后端测试（跳过覆盖率阈值）
test-backend:
	@echo "$(GREEN)▶ 后端测试...$(RESET)"
	cd backend && $(BACKEND_VENV)/pytest tests/test_quant_algorithms.py \
		tests/test_health.py -v --tb=short --no-cov 2>&1

## test-frontend: 运行前端单元测试
test-frontend:
	@echo "$(GREEN)▶ 前端测试...$(RESET)"
	cd frontend && npx vitest run

## test-api: 快速 API 冒烟测试（需要服务运行）
test-api:
	@echo "$(GREEN)▶ API 冒烟测试...$(RESET)"
	@curl -sf http://localhost:8000/health | python3 -m json.tool
	@echo ""
	@curl -sf -X POST http://localhost:8000/api/v1/auth/token \
		-d "username=admin&password=admin123" | python3 -m json.tool

# ── 构建镜像 ──────────────────────────────────────────────────

## build-prod: 仅构建生产镜像（不启动）
build-prod:
	@echo "$(GREEN)▶ 构建后端生产镜像...$(RESET)"
	docker build -t quantbot-backend:latest --target production backend/
	@echo "$(GREEN)▶ 构建前端生产镜像...$(RESET)"
	docker build -t quantbot-frontend:latest --target production frontend/
	@echo "$(GREEN)✓ 镜像构建完成$(RESET)"
	@docker images | grep quantbot

# ── Shell & 调试 ──────────────────────────────────────────────

## shell-backend: 进入后端容器 shell
shell-backend:
	$(COMPOSE_DEV) exec backend /bin/bash 2>/dev/null || \
	$(COMPOSE_PROD) exec backend /bin/sh

## shell-db: 进入数据库 psql
shell-db:
	$(COMPOSE_DEV) exec timescaledb psql -U $${DB_USER:-quantbot} $${DB_NAME:-quantbot} 2>/dev/null || \
	$(COMPOSE_PROD) exec timescaledb psql -U $${DB_USER:-quantbot} $${DB_NAME:-quantbot}

## seed-check: 验证种子数据是否正确写入
seed-check:
	@echo "$(GREEN)▶ 检查种子用户...$(RESET)"
	$(COMPOSE_DEV) exec timescaledb psql -U $${DB_USER:-quantbot} $${DB_NAME:-quantbot} \
		-c "SELECT id, email, role, created_at FROM users;" 2>/dev/null || \
	$(COMPOSE_PROD) exec timescaledb psql -U $${DB_USER:-quantbot} $${DB_NAME:-quantbot} \
		-c "SELECT id, email, role, created_at FROM users;"

## gen-secret: 生成随机 SECRET_KEY（用于 .env）
gen-secret:
	@echo "SECRET_KEY=$(shell openssl rand -hex 32)"
