// 后端 API 桩：用 page.route 把 /api/v1/** 全部拦下来，返回 fixtures/ 里的固定夹具。
//
// 为什么不连真后端（契约 §2.1）：冒烟测的是前端流程能不能走通，不是后端对不对
// —— 后端自己有 2400+ 用例。连真后端要起 Postgres + Redis + Celery，还会因为
// 「今天没有行情数据」这类与代码无关的原因变红；那种红没人会去查，
// 查几次之后所有人都开始忽略它，测试就死了。
//
// 夹具的形状来自后端真实响应模型，对照表见 fixtures/README.md。

import type { Page, Route } from "@playwright/test"

import { CURRENT_USER, LOGIN_FAILURE, LOGIN_TOKEN, ADMIN_CREDENTIALS } from "./auth"
import { BACKTEST_RESULT, STRATEGY_PRESETS } from "./backtest"
import { ORDERS, RISK_SUMMARY, TRADING_MODE } from "./dashboard"
import {
  FACTOR_ANALYSIS, FACTOR_FITNESS, FACTOR_LIST, FACTOR_STRATEGY_METHODS,
  FORMULA_FACTOR, FORMULA_META,
} from "./factor"
import {
  INDICATORS, MARKET_OVERVIEW, SPOT_QUOTES, SYMBOL_SEARCH, barsResponse, latestBar,
} from "./market"
import { ACCOUNT, ATTRIBUTION, OPTIMIZE_RESULT, POSITIONS, REBALANCE_PREVIEW } from "./portfolio"
import {
  SCREENER_MOVERS, SCREENER_PRESETS, SCREENER_RUN, SCREENER_SECTORS,
} from "./screener"

type Handler = (route: Route, url: URL) => Promise<void> | void

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  })
}

/**
 * 登录端点：按提交的口令分流，成功给 Token，失败给 401。
 *
 * 让桩自己判断而不是让用例各自覆盖路由，是为了让「登录失败」这条用例
 * 走的是和成功用例完全相同的桩，两者的差异只有输入。
 */
const handleLogin: Handler = async (route) => {
  const body = route.request().postData() ?? ""
  const submitted = new URLSearchParams(body)
  const ok =
    submitted.get("username") === ADMIN_CREDENTIALS.username &&
    submitted.get("password") === ADMIN_CREDENTIALS.password
  await json(route, ok ? LOGIN_TOKEN : LOGIN_FAILURE, ok ? 200 : 401)
}

/**
 * 会产生真实委托的端点。
 *
 * 冒烟测**绝不**走到这里：下单必须由人确认（见项目「Copilot 动作边界」约定）。
 * 与其不挂桩让它落到 404 那样看起来像「接口没实现」，不如显式回一个刺眼的错误
 * —— 万一哪天有人在用例里点了「确认执行」，失败信息会直接说清楚问题是什么。
 */
const refuseOrderSubmission: Handler = async (route, url) => {
  await json(
    route,
    { detail: `E2E 冒烟不得触达下单端点：${url.pathname}。产生委托的动作必须人工确认。` },
    501,
  )
}

/** `METHOD /pathname` → 处理函数。pathname 是精确匹配，查询串在 handler 里读。 */
const ROUTES: Record<string, Handler> = {
  // ── 认证 ──
  "POST /api/v1/auth/token": handleLogin,
  "GET /api/v1/auth/me": (route) => json(route, CURRENT_USER),

  // ── 行情 ──
  "GET /api/v1/bars": (route, url) =>
    json(
      route,
      barsResponse(
        url.searchParams.get("symbol") ?? "AAPL",
        url.searchParams.get("market") ?? "US",
        url.searchParams.get("frequency") ?? "1d",
      ),
    ),
  "GET /api/v1/bars/latest": (route) => json(route, latestBar()),
  "GET /api/v1/bars/market-overview": (route) => json(route, MARKET_OVERVIEW),
  "GET /api/v1/bars/spot": (route) => json(route, SPOT_QUOTES),
  "GET /api/v1/bars/indicators": (route) => json(route, INDICATORS),
  "GET /api/v1/bars/symbols/search": (route) => json(route, SYMBOL_SEARCH),

  // ── 策略 / 回测 ──
  "GET /api/v1/strategies/presets": (route) => json(route, STRATEGY_PRESETS),
  "POST /api/v1/backtests/run": (route) => json(route, BACKTEST_RESULT),

  // ── 选股器 ──
  "POST /api/v1/screener/run": (route) => json(route, SCREENER_RUN),
  "GET /api/v1/screener/presets": (route) => json(route, SCREENER_PRESETS),
  "GET /api/v1/screener/sectors": (route) => json(route, SCREENER_SECTORS),
  "GET /api/v1/screener/movers": (route) => json(route, SCREENER_MOVERS),

  // ── 组合 ──
  "POST /api/v1/portfolio/optimize": (route) => json(route, OPTIMIZE_RESULT),
  "POST /api/v1/portfolio/rebalance/preview": (route) => json(route, REBALANCE_PREVIEW),
  "POST /api/v1/portfolio/rebalance/execute": refuseOrderSubmission,
  "GET /api/v1/positions": (route) => json(route, POSITIONS),
  "GET /api/v1/positions/account": (route) => json(route, ACCOUNT),
  "GET /api/v1/orders/attribution": (route) => json(route, ATTRIBUTION),
  "POST /api/v1/orders": refuseOrderSubmission,

  // ── 仪表盘（登录后的落地页，不做断言，只为不飘 404）──
  "GET /api/v1/orders": (route) => json(route, ORDERS),
  "GET /api/v1/orders/trading-mode": (route) => json(route, TRADING_MODE),
  "GET /api/v1/risk/summary": (route) => json(route, RISK_SUMMARY),

  // ── 因子研究 ──
  "GET /api/v1/quant/factor/list": (route) => json(route, FACTOR_LIST),
  "POST /api/v1/quant/factor/analyze": (route) => json(route, FACTOR_ANALYSIS),
  "GET /api/v1/quant/factor/formula/meta": (route) => json(route, FORMULA_META),
  "POST /api/v1/quant/factor/formula": (route) => json(route, FORMULA_FACTOR),
  "POST /api/v1/quant/factor/fitness": (route) => json(route, FACTOR_FITNESS),
  "GET /api/v1/factors/strategy/methods": (route) => json(route, FACTOR_STRATEGY_METHODS),
}

/**
 * 装上 API 桩。
 *
 * 未登记的 /api/v1 路径一律回 404 + `detail`（FastAPI 的错误体形状），
 * 前端的 ApiError 会照常抛出、组件走错误态 —— 这比返回一个空对象强：
 * 空对象会让 `.map` 之类的调用炸在渲染里，堆栈还指不到真正的原因。
 * 同时把路径打到 stdout，便于新增页面时一眼看出还缺哪个桩。
 */
export async function stubApi(page: Page): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url())
    const key = `${route.request().method()} ${url.pathname}`
    const handler = ROUTES[key]

    if (!handler) {
      console.warn(`[e2e] 未挂桩的接口：${key} —— 已返回 404`)
      await json(route, { detail: `E2E 未挂桩：${key}` }, 404)
      return
    }

    await handler(route, url)
  })
}
