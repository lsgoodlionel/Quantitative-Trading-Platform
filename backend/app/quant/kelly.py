"""
凯利准则 (Kelly Criterion) 仓位优化

理论基础:
  单资产: f* = (p·b - q) / b
    其中: p = 胜率, q = 1-p = 败率, b = 盈亏比 (avg_win / avg_loss)

  等价公式（用期望和方差）:
    f* = μ / σ²  (对数正态近似)

  分数凯利 (Fractional Kelly):
    建议实际使用 f = f* / 2 或 f* / 4（降低破产风险和回撤）

多资产凯利 (矩阵形式):
  f⃗* = Σ⁻¹ · μ⃗
  其中 Σ = 协方差矩阵, μ = 期望超额收益向量

配置参数:
  win_rate         — 胜率 (0~1)，如 0.55
  avg_win          — 平均盈利（相对金额），如 150
  avg_loss         — 平均亏损（相对金额），如 100
  fraction         — 分数凯利比例，如 0.5 = 半凯利
  max_f            — 仓位上限，如 0.25 (防止过度集中)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class KellyResult:
    """凯利准则计算结果。"""
    win_rate: float
    avg_win: float
    avg_loss: float
    odds_ratio: float      # 盈亏比 b = avg_win / avg_loss
    edge: float            # 期望值 = p·b - q

    full_kelly: float      # 完整凯利 f*
    half_kelly: float      # 半凯利 f*/2（推荐）
    quarter_kelly: float   # 四分之一凯利 f*/4（保守）
    recommended: float     # 实际推荐仓位（考虑上限）

    # 连续公式近似
    kelly_continuous: float  # f* = μ/σ² (对数正态)

    # 破产风险分析
    ruin_probability_full: float    # 使用全凯利的破产概率
    ruin_probability_half: float    # 使用半凯利的破产概率

    # 情景分析（不同仓位比例的期望对数增长）
    growth_curve: list[dict]  # [{f, expected_log_growth}]


def compute_kelly(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.5,
    max_f: float = 0.25,
) -> KellyResult:
    """
    计算凯利仓位及情景分析。

    参数说明:
        win_rate — 历史胜率，如 0.55 (55%)
        avg_win  — 平均每次盈利，如 150（单位可为任意，关键是比例）
        avg_loss — 平均每次亏损（取正数），如 100
        fraction — 分数凯利比例，如 0.5=半凯利（推荐）
        max_f    — 最大允许仓位，如 0.25=25%

    抛出:
        ValueError — 参数范围错误
    """
    if not 0 < win_rate < 1:
        raise ValueError(f"胜率 win_rate 必须在 (0, 1) 内，当前: {win_rate}")
    if avg_win <= 0:
        raise ValueError(f"avg_win 必须 > 0，当前: {avg_win}")
    if avg_loss <= 0:
        raise ValueError(f"avg_loss 必须 > 0，当前: {avg_loss}")

    loss_rate = 1.0 - win_rate
    b = avg_win / avg_loss  # 盈亏比
    edge = win_rate * b - loss_rate

    # 完整凯利公式
    full_kelly = edge / b if b > 0 else 0.0
    full_kelly = max(0.0, full_kelly)  # 负值→不交易

    half_kelly = full_kelly / 2
    quarter_kelly = full_kelly / 4
    recommended = min(full_kelly * fraction, max_f)

    # 连续近似: 假设回报率服从正态 N(μ, σ²)
    # 近似: μ ≈ edge * avg_loss，σ ≈ avg_loss * sqrt(p*(1-p)) * b
    mu_approx = win_rate * avg_win - loss_rate * avg_loss
    sigma_approx = avg_loss * b * float(np.sqrt(win_rate * loss_rate))
    kelly_continuous = mu_approx / (sigma_approx ** 2) if sigma_approx > 0 else 0.0

    # 破产概率（简单模型，假设下注均匀）
    # 使用 Gambler's ruin: P(ruin) ≈ ((1-p)/p)^(1/f*) 的简化估计
    # 实际量化中通常用蒙特卡洛，这里用解析近似
    def ruin_prob(f: float) -> float:
        if f <= 0:
            return 0.0
        # 每期对数期望: E[ln(1 + f·r)]
        # 近似破产概率
        p_up = win_rate
        p_dn = loss_rate
        w = f * b  # 相对盈利
        l = f      # 相对亏损
        ratio = (p_dn * l) / (p_up * w) if (p_up * w) > 0 else 1.0
        return float(min(1.0, ratio ** 10))  # 简化估计，10次迭代近似

    ruin_full = ruin_prob(full_kelly)
    ruin_half = ruin_prob(half_kelly)

    # 情景分析: 不同仓位比例的期望对数增长 E[ln(1+f*r)]
    growth_curve = []
    for pct in range(0, 101, 5):
        f = pct / 100.0
        # E[ln(1+f·b)] * p + E[ln(1-f)] * q
        if f * b >= 1 or f >= 1:
            log_growth = -float("inf")
        else:
            log_growth = win_rate * float(np.log(1 + f * b)) + loss_rate * float(np.log(1 - f))
        growth_curve.append({"f": f, "expected_log_growth": round(log_growth, 6)})

    return KellyResult(
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        odds_ratio=b,
        edge=edge,
        full_kelly=full_kelly,
        half_kelly=half_kelly,
        quarter_kelly=quarter_kelly,
        recommended=recommended,
        kelly_continuous=kelly_continuous,
        ruin_probability_full=ruin_full,
        ruin_probability_half=ruin_half,
        growth_curve=growth_curve,
    )
