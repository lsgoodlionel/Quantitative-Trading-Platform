# V3 Wave A-a 契约：公式因子 → 策略适配器（G1）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **G1** · Agent-Aa
> **前置已就绪**：Wave K-d 已实现 `FormulaFactorAlphaModel` 与 `FrameworkStrategy`
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，且不得修改 `tests/regression/` 任何文件。

---

## 一、V3 记录的断链，以及现在还差什么

V3 把 G1 描述为「打通研究→交易主动脉」，因为**因子挖掘的结果只能看，不能交易**。

Wave K-d 已经把**引擎侧**补齐了：

| 已有（K-d 交付） | 位置 |
|---|---|
| `FormulaFactorAlphaModel` — RPN 公式因子 → 按分位阈值出 `Insight` | `app/engine/framework/factor_alpha.py` |
| `FrameworkStrategy` — Alpha → PCM → Risk → Execution 串成可跑策略 | `app/engine/framework/strategy.py` |
| `OptimizerPCM` — 桥接既有 8 种组合优化方法 | `app/engine/framework/optimizer_pcm.py` |

**所以本契约不需要再写引擎代码**。还差的是三处「接口暴露」：

1. 因子策略**进不了 `STRATEGY_REGISTRY`** —— 注册表的值是 `type`，靠 `params: dict` 构造；
   而 `FrameworkStrategy` 的构造参数是三个模型**对象**。K-d 已明确指出这一点并建议单开通道。
2. 因子库条目 / 挖掘结果**没有「一键回测」「注册为策略」入口**。
3. 实验记录器没有 `promote_to_strategy`（全库 grep 零命中）。

---

## 二、设计

### 2.1 不要硬塞进 `STRATEGY_REGISTRY`

`STRATEGY_REGISTRY: dict[str, type]` 的契约是「类 + params dict」。
把 `FrameworkStrategy` 塞进去需要一个能从 dict 还原三个模型对象的工厂，
会把注册表的语义搞脏，且 16 个 preset 的加载路径也要跟着改。

**改为新增一个平行的工厂函数**：

```python
# app/strategy/factor_strategy.py（新文件）

@dataclass(frozen=True)
class FactorStrategySpec:
    """一条「因子 → 策略」的完整描述，可序列化、可持久化、可重放。"""
    formula: str                       # RPN 表达式
    universe: list[str]                # 标的池
    long_quantile: float = 0.2         # 进入多头的分位阈值
    short_quantile: float | None = None  # None = 纯多头
    rebalance_days: int = 5
    portfolio_method: str = "equal_weight"   # 或既有 8 种优化方法名
    max_positions: int | None = None

def build_factor_strategy(spec: FactorStrategySpec) -> FrameworkStrategy:
    """把 spec 装配成可直接喂给 PortfolioBacktestEngine 的策略。"""
```

`FactorStrategySpec` 是**纯数据**，这让它能：存进实验记录器、经 API 往返、被前端编辑、
被 I2（自动因子循环）批量生成 —— 全部不必碰引擎对象。

### 2.2 API

```
POST /api/v1/factors/strategy/backtest     FactorStrategySpec + 回测区间 → 回测结果
POST /api/v1/factors/strategy/promote      FactorStrategySpec + name    → 存为命名策略
GET  /api/v1/factors/strategy              列出已注册的因子策略
DELETE /api/v1/factors/strategy/{name}
```

**「一键回测」= 前端把因子库条目的公式填进 spec 直接打第一个端点**，无需先注册。
这是 V3 想要的「主动脉」：看到一个因子 → 立刻知道它能不能赚钱。

### 2.3 `promote_to_strategy`

`app/quant/experiments/recorder.py` 增加：

```python
async def promote_to_strategy(
    redis, experiment_id: str, name: str, spec: FactorStrategySpec
) -> str:
    """把一条实验记录提升为命名策略，回写实验记录的 promoted_strategy 字段。"""
```

⚠️ **实验记录器有 `MAX_RECORDS = 500` 的滚动淘汰**（`recorder.py:34`）。
被提升为策略的记录如果被淘汰，策略就成了孤儿。
**因此命名策略必须独立存储，不能只存实验 id 的引用** —— 存完整 `FactorStrategySpec`。

---

## 三、前端

- 因子库条目 / 挖掘结果行：新增「回测」与「注册为策略」两个动作
- 新增（或复用 `Strategies.tsx`）因子策略列表，能编辑 spec 并重跑

⚠️ **不要编辑共享文件**（`App.tsx` / `Sidebar.tsx` / `types/index.ts` / `router.py`），
需要改动时在报告里给集成片段。

---

## 四、验收

```
1. tests/regression 146 用例全绿
2. tests/test_factor_strategy.py:
   - FactorStrategySpec → build_factor_strategy → 可直接跑 PortfolioBacktestEngine
   - spec 往返序列化（dict ↔ dataclass）无损
   - long_quantile / short_quantile / rebalance_days 各自生效
   - portfolio_method 走 OptimizerPCM 时权重和为 1
3. tests/test_api_factor_strategy.py: 四个端点的正常/异常路径
4. 端到端：一条 RPN 公式 → 回测出净值曲线与指标（断言有成交、指标非 NaN）
5. 被提升的策略在实验记录被淘汰后仍可加载（断言不依赖实验 id）
6. ruff check app tests → All checks passed! · npm run lint/type-check/test/build 全过
7. pytest -q 全绿
```

## 五、不做

- 因子策略的实盘运行（实盘接线是 Wave L-d 的范围）
- LLM 自动因子循环（I2，Wave C）
- 因子策略的参数寻优（可用既有 hyperopt，但接线不在本期）
