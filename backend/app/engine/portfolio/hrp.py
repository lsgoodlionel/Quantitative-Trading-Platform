"""
HRP 层次风险平价 (D4)

López de Prado (2016) 的层次风险平价：
1. 由收益相关性构造距离矩阵 d = sqrt((1 − ρ)/2)
2. scipy 层次聚类（single/complete/ward…）得到聚类树
3. 准对角化（quasi-diagonalization）：按聚类顺序重排标的
4. 递归二分（recursive bisection）：自顶向下按「反方差」在两个子簇间分配权重

优点：无需协方差矩阵求逆，对估计误差更稳健，小样本表现更好。

参考签名（不复制实现）:
- refs/PyPortfolioOpt/pypfopt/hierarchical_portfolio.py HRPOpt
  _get_cluster_var / _get_quasi_diag / _raw_hrp_allocation / optimize
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, to_tree
from scipy.spatial.distance import squareform

VALID_LINKAGE = ("single", "complete", "average", "ward")


# ── 聚类工具 ──────────────────────────────────────────────────

def _cluster_var(cov: pd.DataFrame, items: list[str]) -> float:
    """簇内反方差组合的方差（越小越「安全」）。"""
    sub = cov.loc[items, items]
    inv_var = 1.0 / np.diag(sub.to_numpy())
    weights = inv_var / inv_var.sum()
    return float(weights @ sub.to_numpy() @ weights)


def _quasi_diag(link: np.ndarray) -> list[int]:
    """按聚类树的先序遍历返回叶子（标的）索引顺序，使相关标的相邻。"""
    return to_tree(link, rd=False).pre_order()


def _recursive_bisection(cov: pd.DataFrame, ordered: list[str]) -> pd.Series:
    """
    自顶向下递归二分：每次把当前簇一分为二，按两子簇反方差比例劈分权重。

    alpha = 1 − var(左) / (var(左) + var(右))，左簇乘 alpha、右簇乘 1−alpha。
    """
    weights = pd.Series(1.0, index=ordered)
    clusters = [ordered]

    while clusters:
        clusters = [
            cluster[start:stop]
            for cluster in clusters
            for start, stop in ((0, len(cluster) // 2), (len(cluster) // 2, len(cluster)))
            if len(cluster) > 1
        ]
        for i in range(0, len(clusters), 2):
            left = clusters[i]
            right = clusters[i + 1]
            left_var = _cluster_var(cov, left)
            right_var = _cluster_var(cov, right)
            alpha = 1.0 - left_var / (left_var + right_var)
            weights[left] *= alpha
            weights[right] *= 1.0 - alpha

    return weights


# ── 主入口 ────────────────────────────────────────────────────

def hrp_weights(
    returns: pd.DataFrame,
    *,
    cov: pd.DataFrame | None = None,
    linkage_method: str = "single",
) -> pd.Series:
    """
    计算 HRP 权重（long-only，和为 1）。

    Args:
        returns: 日收益 DataFrame（index=日期, columns=symbol），用于估计相关性
        cov: 年化协方差 DataFrame；缺省时由 returns 估计（未年化，不影响相对权重）
        linkage_method: scipy 层次聚类连接方式

    Returns:
        pd.Series（index=symbol）—— 按 symbol 字典序排序，便于回显对齐
    """
    if linkage_method not in VALID_LINKAGE:
        raise ValueError(f"linkage_method 必须为 {VALID_LINKAGE} 之一")
    if returns.shape[1] < 2:
        raise ValueError("HRP 至少需要 2 个资产")

    corr = returns.corr()
    cov_used = cov if cov is not None else returns.cov()
    # 对齐列顺序，防止 cov 与 returns 顺序不一致
    symbols = list(corr.columns)
    cov_used = cov_used.reindex(index=symbols, columns=symbols)

    # 距离矩阵 d = sqrt((1 − ρ)/2)，clip 消除浮点噪声
    dist = np.sqrt(np.clip((1.0 - corr.to_numpy()) / 2.0, 0.0, 1.0))
    condensed = squareform(dist, checks=False)

    link = linkage(condensed, method=linkage_method)
    sort_ix = _quasi_diag(link)
    ordered = [symbols[i] for i in sort_ix]

    raw = _recursive_bisection(cov_used, ordered)
    weights = raw / raw.sum()
    return weights.sort_index()
