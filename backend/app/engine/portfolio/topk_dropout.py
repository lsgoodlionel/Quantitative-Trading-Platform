"""
Topk-Dropout 组合构建 (Wave-3 / D6)

把横截面打分（因子值 / 模型分数）转成可交易的轮动组合：

  · 持有分数最高的 topK 只标的
  · 每期最多剔除分数最差的 n_drop 只、买入新标的（控换手）
  · 最短持仓期 hold_thresh（低于则本期不卖，抑制来回打脸）
  · 资金度 risk_degree（投入比例，其余留现金）

内置一个轻量向量化回测：给定"再平衡日 × 标的"的分数面板与收盘价面板，
逐期生成持仓、买卖清单、换手率、组合净值与绩效指标。

参考算法（只读，未复制代码）：
  refs/qlib/qlib/contrib/strategy/signal_strategy.py :: TopkDropoutStrategy
差异：参考在 qlib Exchange/Position 框架内逐单撮合；此处为自包含的等权轮动
回测，用 pandas/numpy 计算，无需交易所对象。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# 一年的近似日历天数，用于年化
_DAYS_PER_YEAR = 365.0
# 数值下限，避免除零
_EPS = 1e-12


# ── 配置 ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TopkConfig:
    topk:         int   = 5
    n_drop:       int   = 1
    hold_thresh:  int   = 1
    risk_degree:  float = 0.95
    method_sell:  str   = "bottom"   # bottom | random
    method_buy:   str   = "top"      # top | random
    random_state: int   = 42

    def __post_init__(self) -> None:
        if self.topk < 1:
            raise ValueError("topk 必须 >= 1")
        if self.n_drop < 0:
            raise ValueError("n_drop 必须 >= 0")
        if not (0.0 < self.risk_degree <= 1.0):
            raise ValueError("risk_degree 必须在 (0, 1]")
        if self.method_sell not in ("bottom", "random"):
            raise ValueError("method_sell 仅支持 bottom | random")
        if self.method_buy not in ("top", "random"):
            raise ValueError("method_buy 仅支持 top | random")


# ── 结果类 ────────────────────────────────────────────────────────

@dataclass
class TopkPeriod:
    date:          str
    holdings:      list[str]
    weights:       dict[str, float]
    buys:          list[str]
    sells:         list[str]
    turnover:      float
    n_holdings:    int
    period_return: float
    equity:        float


@dataclass
class TopkDropoutResult:
    topk:         int
    n_drop:       int
    hold_thresh:  int
    risk_degree:  float
    method_sell:  str
    method_buy:   str
    n_periods:    int
    periods:      list[TopkPeriod]
    equity_curve: list[dict]      # [{date, equity}]
    metrics:      dict            # 汇总绩效


# ── 单期再平衡决策 ────────────────────────────────────────────────

def _sample(pool: list[str], n: int, rng: np.random.Generator) -> list[str]:
    if n <= 0 or not pool:
        return []
    if n >= len(pool):
        return list(pool)
    return rng.choice(pool, size=n, replace=False).tolist()


def _decide_holdings(
    last: list[str],
    scores: pd.Series,
    cfg: TopkConfig,
    held_days: dict[str, int],
    rng: np.random.Generator,
) -> tuple[list[str], list[str], list[str]]:
    """返回 (新持仓, 买入清单, 卖出清单)。逻辑对齐 qlib TopkDropoutStrategy。"""
    ranked = scores.sort_values(ascending=False)
    universe = ranked.index.tolist()
    last = [s for s in last if s in scores.index]     # 丢弃本期无分数的标的
    n_last = len(last)
    last_set = set(last)

    # 1. 候选买入（当前未持有）
    n_new = cfg.n_drop + cfg.topk - n_last
    not_held = [s for s in universe if s not in last_set]
    if cfg.method_buy == "random":
        candi = [s for s in universe[: cfg.topk] if s not in last_set]
        today = _sample(candi, max(n_new, 0), rng)
    else:
        today = not_held[: max(n_new, 0)]

    # 2. 决定卖出（避免卖高买低：只在 last∪today 的底部里挑）
    # 卖出数受实际可替换候选数量约束——无新候选可买时不卖，避免「卖出即原样买回」空转换手
    keep_or_buy = last_set | set(today)
    comb = [s for s in universe if s in keep_or_buy]
    max_drop = min(cfg.n_drop, len(today)) if n_last >= cfg.topk else cfg.n_drop
    if max_drop <= 0:
        sell: list[str] = []
    elif cfg.method_sell == "random":
        sellable = [s for s in last if held_days.get(s, 0) >= cfg.hold_thresh]
        sell = _sample(sellable, max_drop, rng)
    else:
        bottom = set(comb[-max_drop:]) if max_drop > 0 else set()
        sell = [s for s in last if s in bottom and held_days.get(s, 0) >= cfg.hold_thresh]
    sell_set = set(sell)

    # 3. 实际买入数量 = 卖出数 + 空缺
    n_buy = len(sell) + cfg.topk - n_last
    buy = today[: max(n_buy, 0)]

    new_holdings = [s for s in last if s not in sell_set] + buy
    if len(new_holdings) > cfg.topk:                  # 超额时保留分数更高者
        new_set = set(new_holdings)
        new_holdings = [s for s in universe if s in new_set][: cfg.topk]
    return new_holdings, buy, sell


def _equal_weights(holdings: list[str], risk_degree: float) -> dict[str, float]:
    if not holdings:
        return {}
    w = risk_degree / len(holdings)
    return {s: w for s in holdings}


def _turnover(prev_w: dict[str, float], new_w: dict[str, float]) -> float:
    names = set(prev_w) | set(new_w)
    return sum(abs(new_w.get(s, 0.0) - prev_w.get(s, 0.0)) for s in names) / 2.0


# ── 回测主循环 ────────────────────────────────────────────────────

def run_topk_dropout(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    config: TopkConfig | None = None,
) -> TopkDropoutResult:
    """
    Parameters
    ----------
    scores : DataFrame  index=再平衡日(升序), columns=标的, 值=分数(越大越好)
    prices : DataFrame  与 scores 同 index/columns, 值=该再平衡日收盘价

    每期在再平衡日观测分数并调仓，持有至下一再平衡日获取收益；末期持有到结束。
    """
    cfg = config or TopkConfig()
    if scores.shape[0] < 2:
        raise ValueError("再平衡期数不足（需 >= 2 个日期）")
    if list(scores.index) != list(prices.index):
        raise ValueError("scores 与 prices 的日期索引必须一致")

    rng = np.random.default_rng(cfg.random_state)
    dates = scores.index.tolist()

    holdings: list[str] = []
    held_days: dict[str, int] = {}
    prev_w: dict[str, float] = {}
    equity = 1.0
    periods: list[TopkPeriod] = []
    returns: list[float] = []

    for i, date in enumerate(dates):
        row = scores.loc[date].dropna()
        if row.empty:
            continue

        holdings, buys, sells = _decide_holdings(holdings, row, cfg, held_days, rng)
        held_days = _advance_holding_days(held_days, holdings, buys)
        new_w = _equal_weights(holdings, cfg.risk_degree)
        turnover = _turnover(prev_w, new_w)

        period_ret = _period_return(prices, dates, i, new_w)
        equity *= 1.0 + period_ret
        returns.append(period_ret)

        periods.append(TopkPeriod(
            date=str(date),
            holdings=list(holdings),
            weights={k: round(v, 6) for k, v in new_w.items()},
            buys=list(buys),
            sells=list(sells),
            turnover=round(turnover, 6),
            n_holdings=len(holdings),
            period_return=round(period_ret, 6),
            equity=round(equity, 6),
        ))
        prev_w = new_w

    equity_curve = [{"date": p.date, "equity": p.equity} for p in periods]
    metrics = _summarize(periods, returns, dates)
    return TopkDropoutResult(
        topk=cfg.topk,
        n_drop=cfg.n_drop,
        hold_thresh=cfg.hold_thresh,
        risk_degree=cfg.risk_degree,
        method_sell=cfg.method_sell,
        method_buy=cfg.method_buy,
        n_periods=len(periods),
        periods=periods,
        equity_curve=equity_curve,
        metrics=metrics,
    )


def _advance_holding_days(held_days: dict[str, int], holdings: list[str], buys: list[str]) -> dict[str, int]:
    """为当前持仓累加持有期计数。

    新买入从 1 起算（已历本次调仓，下次决策时已持有满 1 期）——若从 0 起算，
    hold_thresh=1 会退化为强制持有 2 期。
    """
    buy_set = set(buys)
    updated: dict[str, int] = {}
    for s in holdings:
        updated[s] = 1 if s in buy_set else held_days.get(s, 0) + 1
    return updated


def _period_return(prices: pd.DataFrame, dates: list, i: int, weights: dict[str, float]) -> float:
    """持有至下一再平衡日的加权收益；末期无后续价格 → 0。"""
    if i + 1 >= len(dates) or not weights:
        return 0.0
    p0 = prices.loc[dates[i]]
    p1 = prices.loc[dates[i + 1]]
    total = 0.0
    for sym, w in weights.items():
        a, b = p0.get(sym), p1.get(sym)
        if a and b and a > 0 and not (pd.isna(a) or pd.isna(b)):
            total += w * (b / a - 1.0)
    return total


def _summarize(periods: list[TopkPeriod], returns: list[float], dates: list) -> dict:
    if not periods:
        return {"total_return": 0.0}
    equity_final = periods[-1].equity
    ret_arr = np.asarray(returns, dtype=float)

    years = max((_to_days(dates[-1]) - _to_days(dates[0])) / _DAYS_PER_YEAR, _EPS)
    periods_per_year = len(periods) / years
    ann_return = equity_final ** (1.0 / years) - 1.0 if equity_final > 0 else -1.0
    ann_vol = float(ret_arr.std()) * np.sqrt(periods_per_year) if ret_arr.size > 1 else 0.0
    sharpe = ann_return / ann_vol if ann_vol > _EPS else 0.0

    turnovers = np.asarray([p.turnover for p in periods], dtype=float)
    holds = np.asarray([p.n_holdings for p in periods], dtype=float)
    return {
        "total_return":  round(equity_final - 1.0, 6),
        "annual_return": round(float(ann_return), 6),
        "annual_vol":    round(float(ann_vol), 6),
        "sharpe":        round(float(sharpe), 4),
        "max_drawdown":  round(_max_drawdown(periods), 6),
        "avg_turnover":  round(float(turnovers.mean()), 6),
        "avg_holdings":  round(float(holds.mean()), 2),
        "win_rate":      round(float((ret_arr > 0).mean()), 4) if ret_arr.size else 0.0,
    }


def _max_drawdown(periods: list[TopkPeriod]) -> float:
    # 拼接起始基准 1.0，使首期即亏损的回撤也计入统计
    equity = np.asarray([1.0] + [p.equity for p in periods], dtype=float)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(dd.min()) if dd.size else 0.0


def _to_days(value) -> float:
    ts = pd.Timestamp(value)
    return ts.value / 8.64e13   # ns → days
