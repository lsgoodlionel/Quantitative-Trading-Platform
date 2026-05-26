"""
主成分分析 (PCA) 因子风险分解

理论基础:
  对收益率协方差矩阵做特征分解: Σ = P·Λ·P^T
  主成分: F_k = P_k^T · r  (第 k 个因子收益率)

  方差解释比例: VEV_k = λ_k / Σλ_i

  风险分解:
    系统性风险 (α) = Σ_k β_{ik}²·Var(F_k)   (前 n_components 因子)
    特质风险 (ε)   = 总方差 - 系统性风险

  因子载荷 (Factor Loading):
    β_{ik} = P_{ik}·√λ_k   (第 i 资产在第 k 因子上的暴露)

应用场景:
  - 股票组合风险因子分析（识别共同风险来源）
  - 降维（用少数因子解释大部分波动）
  - 统计套利（因子中性化）
  - 异常检测（残差大的资产可能存在特殊机会）

配置参数:
  returns_matrix — 收益率矩阵，shape=(T, N)，T=时间，N=资产数
  asset_names    — 资产名称列表
  n_components   — 保留的主成分数量（默认 min(N, 5)）
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PCAResult:
    """PCA 因子分析结果。"""
    n_assets: int
    n_observations: int
    n_components: int

    # 特征值 / 解释方差
    eigenvalues: list[float]
    explained_variance_ratio: list[float]   # 每个主成分解释的方差比例
    cumulative_variance_ratio: list[float]  # 累计解释方差

    # 因子载荷矩阵 shape=(n_assets, n_components)
    # 第 [i][k] 元素 = 第 i 资产在第 k 因子上的载荷
    factor_loadings: list[list[float]]

    # 因子收益率时间序列 shape=(n_observations, n_components)
    factor_returns: list[list[float]]

    # 各资产风险分解
    risk_decomposition: list[dict]   # [{asset, systematic_pct, idiosyncratic_pct}]

    # 相关性热图数据
    asset_names: list[str]
    correlation_matrix: list[list[float]]

    # PC1 解释（按载荷绝对值排序的资产）
    pc1_top_contributors: list[dict]   # [{asset, loading}]


def analyze_pca(
    returns_matrix: list[list[float]],
    asset_names: list[str] | None = None,
    n_components: int | None = None,
) -> PCAResult:
    """
    对多资产收益率矩阵做 PCA 因子分解。

    参数说明:
        returns_matrix — 收益率矩阵，每行=一个时间点，每列=一个资产
                         示例: [[r1_t1, r2_t1], [r1_t2, r2_t2], ...]
        asset_names    — 资产名称，默认 ['Asset_0', 'Asset_1', ...]
        n_components   — 主成分数量，默认 min(N, 5)

    抛出:
        ValueError — 矩阵维度不足
    """
    R = np.asarray(returns_matrix, dtype=float)  # shape=(T, N)
    if R.ndim != 2 or R.shape[0] < 5:
        raise ValueError(f"收益率矩阵须为 (T, N) 二维数组且 T >= 5，当前形状: {R.shape}")

    T, N = R.shape
    names = asset_names if asset_names and len(asset_names) == N else [f"Asset_{i}" for i in range(N)]

    k = min(n_components or 5, N, T - 1)

    # 中心化
    R_centered = R - R.mean(axis=0)

    # 协方差矩阵特征分解
    cov = np.cov(R_centered.T)
    eigenvalues_full, eigenvectors_full = np.linalg.eigh(cov)
    # eigh 返回升序，翻转为降序
    idx = np.argsort(eigenvalues_full)[::-1]
    eigenvalues_all = eigenvalues_full[idx]
    eigenvectors_all = eigenvectors_full[:, idx]

    # 取前 k 个
    eigenvalues = eigenvalues_all[:k]
    eigenvectors = eigenvectors_all[:, :k]

    total_var = float(np.sum(eigenvalues_all))
    evr = (eigenvalues / total_var).tolist()
    cumulative_evr = float(np.cumsum(evr)[-1])  # noqa: F841

    # 因子载荷: F_k = P_k * sqrt(λ_k)
    loadings = eigenvectors * np.sqrt(eigenvalues)  # shape=(N, k)

    # 因子收益率: shape=(T, k)
    factor_ret = R_centered @ eigenvectors  # shape=(T, k)

    # 风险分解（每个资产的系统性风险比例）
    systematic_var = np.sum(loadings ** 2, axis=1)  # shape=(N,)
    total_asset_var = np.var(R, axis=0)
    risk_decomp = [
        {
            "asset": names[i],
            "systematic_pct": round(float(min(systematic_var[i] / total_asset_var[i], 1.0)), 4)
            if total_asset_var[i] > 0 else 0.0,
            "idiosyncratic_pct": round(float(max(1.0 - systematic_var[i] / total_asset_var[i], 0.0)), 4)
            if total_asset_var[i] > 0 else 1.0,
        }
        for i in range(N)
    ]

    # PC1 贡献者（按第一主成分载荷绝对值排序）
    pc1_loadings = loadings[:, 0].tolist()
    pc1_top = sorted(
        [{"asset": names[i], "loading": round(float(loadings[i, 0]), 4)} for i in range(N)],
        key=lambda d: abs(d["loading"]),  # type: ignore[return-value]
        reverse=True,
    )

    # 相关性矩阵
    corr = np.corrcoef(R.T)

    return PCAResult(
        n_assets=N,
        n_observations=T,
        n_components=k,
        eigenvalues=eigenvalues.tolist(),
        explained_variance_ratio=evr,
        cumulative_variance_ratio=np.cumsum(evr).tolist(),
        factor_loadings=loadings.tolist(),
        factor_returns=factor_ret.tolist(),
        risk_decomposition=risk_decomp,
        asset_names=names,
        correlation_matrix=corr.tolist(),
        pc1_top_contributors=pc1_top,
    )
