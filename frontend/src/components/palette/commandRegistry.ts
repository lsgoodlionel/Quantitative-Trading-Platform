// ── 命令面板条目注册表（V3 · H3）────────────────────────────────
//
// 页面条目的 `to` 必须带上 `?tab=`：B-b 把 13 个页面并成了 9 个主页，
// 「组合优化器」这类原独立页现在是 `/portfolio` 的一个 Tab，
// 只跳 `/portfolio` 会落在「持仓分析」上，等于没跳到用户想去的地方。
import { DEFAULT_WATCHLIST } from "@/pages/market/config"
import type { CommandItem } from "./commandTypes"

// ── 页面 ──────────────────────────────────────────────────────

export const PAGE_COMMANDS: readonly CommandItem[] = [
  { id: "page-dashboard", kind: "page", label: "仪表盘", hint: "总览与智能引导", to: "/", keywords: ["dashboard", "home", "首页", "总览"] },

  { id: "page-market-quote", kind: "page", label: "行情查询", hint: "K 线与技术指标", to: "/market?tab=quote", keywords: ["market", "quote", "kline", "行情", "K线"] },
  { id: "page-market-watchlist", kind: "page", label: "自选行情", hint: "自选池实时报价", to: "/market?tab=watchlist", keywords: ["watchlist", "自选"] },
  { id: "page-market-events", kind: "page", label: "事件期权", hint: "财报日历与期权链", to: "/market?tab=events", keywords: ["events", "option", "期权", "财报", "事件"] },

  { id: "page-screener", kind: "page", label: "选股器", hint: "按条件筛选标的", to: "/screener", keywords: ["screener", "filter", "筛选", "选股", "发现"] },

  { id: "page-research-factor", kind: "page", label: "因子研究", hint: "公式因子、因子库、适应度", to: "/research?tab=factor", keywords: ["factor", "alpha", "因子", "研究"] },
  { id: "page-research-ml", kind: "page", label: "算法实验室", hint: "机器学习与序列模型", to: "/research?tab=ml", keywords: ["ml", "algo", "算法", "机器学习", "实验室"] },

  { id: "page-strategies", kind: "page", label: "策略列表", hint: "已注册策略与参数", to: "/strategies", keywords: ["strategy", "strategies", "策略"] },
  { id: "page-backtest", kind: "page", label: "回测验证", hint: "回测、参数寻优、样本外、稳健性", to: "/backtest", keywords: ["backtest", "回测", "验证", "寻优"] },

  { id: "page-portfolio-holdings", kind: "page", label: "持仓分析", hint: "当前持仓与盈亏", to: "/portfolio?tab=holdings", keywords: ["portfolio", "holdings", "持仓", "组合"] },
  { id: "page-portfolio-optimizer", kind: "page", label: "组合优化器", hint: "权重优化与调仓预览", to: "/portfolio?tab=optimizer", keywords: ["optimizer", "optimize", "组合优化", "权重", "调仓"] },

  { id: "page-trading-live", kind: "page", label: "策略交易", hint: "模拟盘/实盘策略实例", to: "/trading?tab=live", keywords: ["live", "trading", "实盘", "模拟盘", "交易"] },
  { id: "page-trading-orders", kind: "page", label: "订单中心", hint: "委托、成交与算法单", to: "/trading?tab=orders", keywords: ["orders", "订单", "委托", "成交"] },

  { id: "page-risk", kind: "page", label: "风险控制", hint: "敞口、限额与保护机制", to: "/risk", keywords: ["risk", "风控", "风险", "敞口"] },
  // 两个 Tab 各留一条：面板要能直达具体 Tab，而不是只跳到页面默认 Tab
  { id: "page-alerts", kind: "page", label: "预警规则", hint: "价格与指标预警", to: "/alerts?tab=rules", keywords: ["alerts", "预警", "报警"] },
  { id: "page-notifications", kind: "page", label: "通知中心", hint: "历史消息与已读状态", to: "/alerts?tab=inbox", keywords: ["notifications", "通知", "消息", "收件箱"] },
  { id: "page-lab", kind: "page", label: "实验室产物", hint: "模型与实验记录", to: "/lab", keywords: ["lab", "artifacts", "产物", "实验"] },

  { id: "page-settings", kind: "page", label: "系统设置", hint: "券商、数据源与通知配置", to: "/settings", keywords: ["settings", "配置", "设置"] },
  { id: "page-settings-models", kind: "page", label: "模型管理", hint: "大模型 provider 与密钥", to: "/settings/models", keywords: ["models", "llm", "模型", "大模型"] },
] as const

// ── 标的 ──────────────────────────────────────────────────────
//
// 复用行情页的默认自选池，不另建一份标的清单：两份清单必然会漂移。
// 面板不做后端标的搜索 —— 那要新端点，且输入即请求会把面板变成搜索框。

export const SYMBOL_COMMANDS: readonly CommandItem[] = DEFAULT_WATCHLIST.map(
  ({ symbol, market, name }): CommandItem => ({
    id: `symbol-${market}-${symbol}`,
    kind: "symbol",
    label: `${symbol} ${name}`,
    hint: `设为当前标的并查看行情`,
    keywords: [symbol, name, market],
    to: "/market?tab=quote",
    symbol: { symbol, market },
  }),
)

// ── 动作 ──────────────────────────────────────────────────────
//
// 边界：动作条目只做「跳转 + 预置状态」，不产生订单、不调写接口。
// 需要确认与副作用的写操作属于 Copilot（B-c）的范围。

export const ACTION_COMMANDS: readonly CommandItem[] = [
  {
    id: "action-new-factor-strategy",
    kind: "action",
    label: "新建因子策略",
    hint: "去因子研究，从公式因子或因子库出发",
    to: "/research?tab=factor",
    keywords: ["factor", "新建", "因子策略"],
  },
  {
    id: "action-full-validation",
    kind: "action",
    label: "运行完整验证",
    hint: "带当前标的去「回测 / 完整验证」",
    to: "/backtest",
    keywords: ["validation", "验证", "回测"],
    withSymbol: true,
  },
  {
    id: "action-analyze-symbol",
    kind: "action",
    label: "分析当前标的",
    hint: "带当前标的去行情页看 K 线与指标",
    to: "/market?tab=quote",
    keywords: ["analyze", "分析", "行情"],
    withSymbol: true,
  },
  {
    id: "action-optimize-portfolio",
    kind: "action",
    label: "优化组合权重",
    hint: "去组合优化器",
    to: "/portfolio?tab=optimizer",
    keywords: ["optimize", "优化", "权重"],
  },
  {
    id: "action-screen-stocks",
    kind: "action",
    label: "开始筛选标的",
    hint: "去选股器",
    to: "/screener",
    keywords: ["screen", "筛选", "选股"],
  },
  {
    id: "action-open-playbook-discovery",
    kind: "action",
    label: "打开「发现→组合」引导",
    hint: "在仪表盘展开该条 Playbook",
    to: "/?playbook=discovery-portfolio",
    keywords: ["playbook", "引导", "组合"],
  },
  {
    id: "action-open-playbook-factor",
    kind: "action",
    label: "打开「因子研究」引导",
    hint: "在仪表盘展开该条 Playbook",
    to: "/?playbook=factor-research",
    keywords: ["playbook", "引导", "因子"],
  },
] as const

export const ALL_COMMANDS: readonly CommandItem[] = [
  ...PAGE_COMMANDS,
  ...SYMBOL_COMMANDS,
  ...ACTION_COMMANDS,
]
