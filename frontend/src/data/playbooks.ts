// ── 三条 Playbook 的步骤定义（V3 · H4）──────────────────────────
//
// 纯数据，无组件依赖 —— 渲染在 PlaybookPanel，存储在 useWorkflowStorage。
// 目标路由一律带上 `?tab=`：B-b 之后「组合优化器」这类原独立页已经变成 Tab，
// 只跳页面根路径会落在默认 Tab 上，等于没送到用户想去的地方。
import type { Playbook, PlaybookId } from "@/components/workflow/playbookTypes"

export const PLAYBOOKS: readonly Playbook[] = [
  {
    id: "single-symbol",
    name: "单标的技术流",
    icon: "📈",
    summary: "盯一只票：看行情 → 选策略 → 回测 → 验证 → 模拟盘",
    steps: [
      {
        id: "pick-symbol",
        title: "选定标的",
        description: "在行情页或选股器里挑一只要研究的股票，选中后它会成为全站的「当前标的」。",
        to: "/market?tab=quote",
        criteria: "地址栏带上了 ?symbol=，顶部命令面板显示的当前标的是它",
      },
      {
        id: "read-quote",
        title: "看行情与指标",
        description: "在行情查询里叠加 RSI / MACD / 布林带等指标，对当前处在什么行情形态先有判断。",
        to: "/market?tab=quote",
        criteria: "能说出这只票现在是趋势市还是震荡市",
        withSymbol: true,
      },
      {
        id: "backtest",
        title: "跑一次回测",
        description: "带着标的去验证页，挑一个与行情形态匹配的策略跑回测。",
        to: "/backtest",
        criteria: "拿到了夏普、最大回撤、胜率三个数",
        withSymbol: true,
      },
      {
        id: "validate",
        title: "样本外与稳健性验证",
        description: "回测好看不代表能用。切到「样本外验证」和「稳健性」，看参数轻微扰动后结果是否还站得住。",
        to: "/backtest",
        criteria: "样本外收益未显著劣化，参数邻域内指标没有断崖",
        withSymbol: true,
      },
      {
        id: "paper",
        title: "模拟盘试运行",
        description: "以模拟盘方式启动策略实例，观察真实行情下的信号频率与滑点。",
        to: "/trading?tab=live",
        criteria: "策略实例在跑，且信号数量与回测量级接近",
        withSymbol: true,
      },
    ],
  },
  {
    id: "discovery-portfolio",
    name: "发现 → 组合流",
    icon: "🧺",
    summary: "从一篮子候选出发：筛选 → 多选 → 组合优化 → 预览调仓 → 确认执行",
    steps: [
      {
        id: "screen",
        title: "条件筛选",
        description: "在选股器里设定市值、估值、动量等条件，把宇宙收敛到几十只以内。",
        to: "/screener",
        criteria: "筛出的候选数量在可人工过一遍的范围内",
      },
      {
        id: "multi-select",
        title: "多选候选标的",
        description: "在结果表里勾选一组要一起做组合的标的，选股器会把它们带到组合优化器。",
        to: "/screener",
        criteria: "至少勾选了 3 只标的",
      },
      {
        id: "optimize",
        title: "组合优化",
        description: "在组合优化器里选择目标（最小方差 / 最大夏普 / 风险平价），算出建议权重。",
        to: "/portfolio?tab=optimizer",
        criteria: "得到一组权重，且单一标的权重没有异常集中",
      },
      {
        id: "preview-rebalance",
        title: "预览调仓",
        description: "对照当前持仓看这组权重意味着买卖多少，注意换手率与交易成本。",
        // 「预览调仓」按钮在 optimizer tab 的优化结果里（RebalancePanel 挂在
        // OptimizerResult 内），不在 holdings tab —— 后者只有持仓明细。
        // 此前指向 holdings，等于把用户送到一个没有这个按钮的页面。
        to: "/portfolio?tab=optimizer",
        criteria: "看过调仓清单，换手率可接受",
      },
      {
        id: "confirm-exec",
        title: "确认执行",
        description: "到订单中心逐笔确认下单。⚠️ 这一步会产生真实委托，Playbook 只负责把你送到这里。",
        to: "/trading?tab=orders",
        criteria: "委托已提交，或已明确决定本轮不调仓",
      },
    ],
  },
  {
    id: "factor-research",
    name: "因子研究流",
    icon: "🔭",
    summary: "从因子出发：建公式/选因子 → 适应度 → 回测 → 注册为策略",
    steps: [
      {
        id: "build-factor",
        title: "建公式因子或选因子库条目",
        description: "在因子研究里用公式构造一个因子，或直接从因子库里挑一个已有条目。",
        to: "/research?tab=factor",
        criteria: "因子能算出值，IC/IR 有初步结果",
      },
      {
        id: "processors",
        title: "截面处理流水线",
        description: "去极值、标准化、中性化 —— 原始因子几乎不能直接用。",
        to: "/research?tab=factor",
        criteria: "处理后因子分布不再被极端值主导",
      },
      {
        id: "fitness",
        title: "成本感知适应度",
        description: "把换手与交易成本算进去看因子还剩多少。很多高 IC 因子在这里被淘汰。",
        to: "/research?tab=factor",
        criteria: "扣成本后适应度仍为正",
      },
      {
        id: "factor-backtest",
        title: "因子回测",
        description: "把因子接成可交易信号跑一遍回测，看它在真实持仓约束下的表现。",
        to: "/backtest",
        criteria: "回测跑通，收益曲线与因子分层结论一致",
      },
      {
        id: "register",
        title: "注册为策略",
        description: "把验证过的因子固化成策略条目，之后就能进模拟盘与实盘流程。",
        to: "/strategies",
        criteria: "策略列表里能看到它",
      },
    ],
  },
] as const

const DEFAULT_PLAYBOOK = PLAYBOOKS[0]

/**
 * 按 id 取 Playbook。
 *
 * id 来自 URL 的 `?playbook=`，属于外部输入：不认识的值一律回落到第一条，
 * 而不是渲染空白面板。
 */
export function getPlaybook(id: string | null | undefined): Playbook {
  return PLAYBOOKS.find((pb) => pb.id === id) ?? DEFAULT_PLAYBOOK
}

export function isPlaybookId(id: string | null | undefined): id is PlaybookId {
  return PLAYBOOKS.some((pb) => pb.id === id)
}
