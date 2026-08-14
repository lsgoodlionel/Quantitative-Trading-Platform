/**
 * QuantBot 冒烟级容量摸底 — k6
 *
 * 这不是性能基准测试。目的只有一个：确认几个只读端点在并发 N 下
 * 不会崩、不会超时，并给出 p50/p95 与错误率的量级。
 *
 * ⚠️⚠️ 只压只读端点。写端点（下单/撤单）会产生真实交易，
 *       AI 端点（LLM/报告/Copilot）会产生真实费用。
 *       下面的 FORBIDDEN 校验在启动时强制执行，不是提醒而是拦截。
 *
 * 用法见同目录 README.md
 */
import http from "k6/http";
import { check } from "k6";
import { Trend, Rate } from "k6/metrics";

// ── 配置 ──────────────────────────────────────────────────────────
const BASE_URL = __ENV.QB_BASE_URL || "http://localhost:8000";
const TOKEN = __ENV.QB_TOKEN || "";
const VUS = parseInt(__ENV.QB_VUS || "10", 10);
const DURATION = __ENV.QB_DURATION || "30s";

/**
 * 只读目标端点。新增前请确认三件事：
 *   1. 是 GET；
 *   2. 不触发下单、撤单、改配置等任何状态变更；
 *   3. 不调用 LLM / 外部付费 API。
 */
const TARGETS = [
  { name: "health",            path: "/health" },
  { name: "api_health",        path: "/api/v1/health" },
  { name: "strategy_presets",  path: "/api/v1/strategies/presets" },
  { name: "strategy_list",     path: "/api/v1/strategies" },
  { name: "factor_library",    path: "/api/v1/quant/factor/library" },
  { name: "universe_indexes",  path: "/api/v1/universe/indexes" },
];

// ── 安全闸门（代码强制，不是靠 README 提醒）──────────────────────
// 经验教训：写在文档里的禁令，总有人在赶时间时跳过。
// 这里在脚本初始化阶段直接抛错，压根跑不起来。
const FORBIDDEN = [
  { pattern: /\/orders(\/|$|\?)/i,        why: "订单端点 — 会产生真实交易" },
  { pattern: /\/live-strategies/i,        why: "实盘策略端点 — 会影响真实持仓" },
  { pattern: /\/broker-config/i,          why: "券商配置 — 状态变更" },
  { pattern: /\/rebalance/i,              why: "再平衡 — 会产生真实交易" },
  { pattern: /\/(llm|ai-reports|copilot)/i, why: "AI 端点 — 会产生真实费用" },
  { pattern: /\/notify|\/notifications/i, why: "通知端点 — 会真的发消息出去" },
  { pattern: /\/factor-mining|\/full-validation|\/robustness/i,
    why: "分钟级重计算任务 — 不属于冒烟范围，会把机器压垮" },
];

for (const t of TARGETS) {
  for (const rule of FORBIDDEN) {
    if (rule.pattern.test(t.path)) {
      throw new Error(
        `[安全闸门] 目标 "${t.name}" (${t.path}) 命中禁止规则：${rule.why}。\n` +
          `负载测试只允许只读端点。请移除该目标，不要绕过这段检查。`,
      );
    }
  }
}

// ── 指标 ──────────────────────────────────────────────────────────
const latency = new Trend("qb_endpoint_latency", true);
const failures = new Rate("qb_endpoint_failures");

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    // 冒烟阈值刻意放宽：这里判定的是「有没有明显问题」，不是性能达标线。
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<2000"],
  },
  // 不上传到 k6 cloud，结果留在本地
  noConnectionReuse: false,
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

const params = {
  headers: Object.assign(
    { Accept: "application/json" },
    TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {},
  ),
  timeout: "30s",
};

export default function () {
  for (const target of TARGETS) {
    const res = http.get(`${BASE_URL}${target.path}`,
      Object.assign({}, params, { tags: { endpoint: target.name } }));

    // 401/403 视为「配置问题」而非服务故障，单独提示，避免误判成容量问题
    const ok = check(res, {
      [`${target.name}: 状态码 2xx`]: (r) => r.status >= 200 && r.status < 300,
    });

    if (!ok && (res.status === 401 || res.status === 403)) {
      console.warn(
        `${target.name} 返回 ${res.status} — 该端点需要鉴权，请设置 QB_TOKEN 环境变量`,
      );
    }

    latency.add(res.timings.duration, { endpoint: target.name });
    failures.add(!ok, { endpoint: target.name });
  }
}

export function handleSummary(data) {
  const m = data.metrics;
  const p = (k, s) => (m[k] && m[k].values[s] != null ? m[k].values[s].toFixed(1) : "n/a");
  const lines = [
    "",
    "════ QuantBot 容量摸底结果 ════",
    `  目标      : ${BASE_URL}`,
    `  并发/时长 : ${VUS} VU / ${DURATION}`,
    `  p50       : ${p("http_req_duration", "med")} ms`,
    `  p95       : ${p("http_req_duration", "p(95)")} ms`,
    `  p99       : ${p("http_req_duration", "p(99)")} ms`,
    `  错误率    : ${m.http_req_failed ? (m.http_req_failed.values.rate * 100).toFixed(2) : "n/a"} %`,
    "",
    "  提示：这是冒烟摸底，数字受本机负载影响很大，",
    "        只在同一台机器上做纵向对比才有意义。",
    "",
  ].join("\n");

  return { stdout: lines };
}
