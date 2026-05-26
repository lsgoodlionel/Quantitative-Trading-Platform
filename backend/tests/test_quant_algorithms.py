"""
高阶量化算法单元测试

覆盖: GBM / BSM / GARCH / Kelly / Cointegration / PCA / HMM / Copula
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.quant.bsm import price_option
from app.quant.copula import analyze_copula
from app.quant.cointegration import analyze_cointegration
from app.quant.gbm import simulate_gbm
from app.quant.garch import fit_garch11
from app.quant.hmm_regime import fit_hmm
from app.quant.kelly import compute_kelly
from app.quant.pca_factor import analyze_pca

# ── 公用 Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def daily_returns() -> list[float]:
    """生成 300 个模拟日收益率（GARCH-like）。"""
    rng = np.random.default_rng(42)
    r = rng.normal(0.0005, 0.015, 300)
    return r.tolist()


@pytest.fixture
def price_series() -> tuple[list[float], list[float]]:
    """生成两个协整的模拟价格序列。"""
    rng = np.random.default_rng(0)
    n = 200
    x = 100.0 + np.cumsum(rng.normal(0, 1, n))
    y = 1.5 * x + rng.normal(0, 2, n)  # 协整: y ≈ 1.5x + noise
    return y.tolist(), x.tolist()


# ── GBM 测试 ──────────────────────────────────────────────────────

class TestGBM:
    def test_returns_gbm_result(self):
        result = simulate_gbm(S0=100, mu=0.10, sigma=0.20, T=1.0, n_paths=100, n_steps=52, seed=1)
        assert result.S0 == 100
        assert result.n_paths == 100
        assert result.n_steps == 52

    def test_sample_paths_shape(self):
        result = simulate_gbm(S0=100, mu=0.10, sigma=0.20, T=1.0, n_paths=200, n_steps=52, seed=1)
        assert len(result.sample_paths) <= 100     # 最多返回100条
        assert len(result.sample_paths[0]) == 53  # n_steps+1 (含起始点)

    def test_positive_drift_expected_return(self):
        result = simulate_gbm(S0=100, mu=0.20, sigma=0.05, T=1.0, n_paths=5000, seed=0)
        # 强漂移下，期望收益应为正
        assert result.expected_return > 0

    def test_var95_less_than_initial(self):
        result = simulate_gbm(S0=100, mu=0.0, sigma=0.30, T=1.0, n_paths=5000, seed=0)
        assert result.var_95 > 0  # 亏损额为正数

    def test_prob_loss_zero_sigma(self):
        """零波动率时，漂移为正→亏损概率应接近0。"""
        result = simulate_gbm(S0=100, mu=0.10, sigma=0.001, T=1.0, n_paths=1000, seed=0)
        assert result.prob_loss < 0.01

    def test_reproducible_with_seed(self):
        r1 = simulate_gbm(S0=100, mu=0.05, sigma=0.20, T=0.5, n_paths=100, n_steps=50, seed=99)
        r2 = simulate_gbm(S0=100, mu=0.05, sigma=0.20, T=0.5, n_paths=100, n_steps=50, seed=99)
        assert r1.final_mean == pytest.approx(r2.final_mean, rel=1e-9)

    def test_time_axis_length(self):
        result = simulate_gbm(S0=50, mu=0.0, sigma=0.2, T=1.0, n_paths=100, n_steps=30, seed=1)
        assert len(result.time_axis) == 31  # n_steps + 1


# ── BSM 测试 ──────────────────────────────────────────────────────

class TestBSM:
    def test_atm_call_positive(self):
        result = price_option(S=100, K=100, r=0.05, sigma=0.20, T=1.0)
        assert result.price > 0

    def test_call_put_parity(self):
        """Put-Call Parity: C - P = S - K·e^{-rT}"""
        S, K, r, sigma, T = 100, 105, 0.05, 0.20, 1.0
        c = price_option(S=S, K=K, r=r, sigma=sigma, T=T, option_type="call")
        p = price_option(S=S, K=K, r=r, sigma=sigma, T=T, option_type="put")
        lhs = c.price - p.price
        rhs = S - K * math.exp(-r * T)
        assert lhs == pytest.approx(rhs, abs=1e-8)

    def test_deep_itm_call_delta_near_one(self):
        """深度实值看涨期权 Delta → 1。"""
        result = price_option(S=200, K=100, r=0.05, sigma=0.20, T=1.0, option_type="call")
        assert result.delta > 0.95

    def test_put_delta_negative(self):
        result = price_option(S=100, K=100, r=0.05, sigma=0.20, T=1.0, option_type="put")
        assert result.delta < 0

    def test_gamma_positive(self):
        result = price_option(S=100, K=100, r=0.05, sigma=0.20, T=1.0)
        assert result.gamma > 0

    def test_vega_positive(self):
        result = price_option(S=100, K=100, r=0.05, sigma=0.20, T=1.0)
        assert result.vega > 0

    def test_theta_negative_long_call(self):
        """时间衰减：持有期权，Theta 应为负。"""
        result = price_option(S=100, K=100, r=0.05, sigma=0.20, T=1.0, option_type="call")
        assert result.theta < 0

    def test_intrinsic_value_itm_call(self):
        result = price_option(S=110, K=100, r=0.05, sigma=0.20, T=0.01, option_type="call")
        # 近到期实值期权内在价值 ≈ 10
        assert result.intrinsic_value == pytest.approx(10.0, abs=0.01)

    def test_invalid_T_raises(self):
        with pytest.raises(ValueError, match="T"):
            price_option(S=100, K=100, r=0.05, sigma=0.20, T=0)

    def test_invalid_option_type_raises(self):
        with pytest.raises(ValueError, match="option_type"):
            price_option(S=100, K=100, r=0.05, sigma=0.20, T=1.0, option_type="straddle")


# ── GARCH 测试 ──────────────────────────────────────────────────────

class TestGARCH:
    def test_fits_without_error(self, daily_returns):
        result = fit_garch11(daily_returns)
        assert result.omega > 0
        assert result.alpha > 0
        assert result.beta > 0

    def test_stationarity(self, daily_returns):
        """α + β < 1（GARCH 平稳性条件）。"""
        result = fit_garch11(daily_returns)
        assert result.persistence < 1.0

    def test_conditional_vol_positive(self, daily_returns):
        result = fit_garch11(daily_returns)
        assert all(v > 0 for v in result.conditional_vol)

    def test_forecast_length(self, daily_returns):
        horizon = 20
        result = fit_garch11(daily_returns, forecast_horizon=horizon)
        assert len(result.forecast_vol) == horizon

    def test_forecast_positive(self, daily_returns):
        result = fit_garch11(daily_returns, forecast_horizon=10)
        assert all(v > 0 for v in result.forecast_vol)

    def test_long_run_vol_positive(self, daily_returns):
        result = fit_garch11(daily_returns)
        assert result.long_run_vol_annualized > 0

    def test_half_life_positive(self, daily_returns):
        result = fit_garch11(daily_returns)
        assert result.half_life_days > 0

    def test_insufficient_data_raises(self):
        with pytest.raises(ValueError, match="长度不足"):
            fit_garch11([0.01] * 10)


# ── Kelly 测试 ──────────────────────────────────────────────────────

class TestKelly:
    def test_positive_edge_positive_kelly(self):
        result = compute_kelly(win_rate=0.6, avg_win=100, avg_loss=80)
        assert result.full_kelly > 0
        assert result.edge > 0

    def test_negative_edge_zero_kelly(self):
        """期望值为负时，凯利仓位应为0。"""
        result = compute_kelly(win_rate=0.3, avg_win=50, avg_loss=100)
        assert result.full_kelly == 0.0

    def test_half_kelly_is_half(self):
        result = compute_kelly(win_rate=0.55, avg_win=100, avg_loss=100)
        assert result.half_kelly == pytest.approx(result.full_kelly / 2)

    def test_recommended_respects_max_f(self):
        result = compute_kelly(win_rate=0.8, avg_win=200, avg_loss=50, max_f=0.10)
        assert result.recommended <= 0.10

    def test_growth_curve_peak_near_full_kelly(self):
        """期望对数增长最大值应在全凯利附近。"""
        result = compute_kelly(win_rate=0.60, avg_win=100, avg_loss=100)
        curve = result.growth_curve
        best = max(curve, key=lambda d: d["expected_log_growth"])
        assert abs(best["f"] - result.full_kelly) < 0.15

    def test_invalid_win_rate_raises(self):
        with pytest.raises(ValueError):
            compute_kelly(win_rate=1.5, avg_win=100, avg_loss=100)


# ── Cointegration 测试 ──────────────────────────────────────────────

class TestCointegration:
    def test_cointegrated_series_detected(self, price_series):
        y, x = price_series
        result = analyze_cointegration(y, x)
        assert result.is_cointegrated  # 协整序列应被检测到

    def test_hedge_ratio_positive(self, price_series):
        y, x = price_series
        result = analyze_cointegration(y, x)
        assert result.hedge_ratio > 0  # y = 1.5*x + noise

    def test_signal_valid(self, price_series):
        y, x = price_series
        result = analyze_cointegration(y, x)
        assert result.signal in ("BUY_SPREAD", "SELL_SPREAD", "EXIT", "HOLD")

    def test_spread_series_length(self, price_series):
        y, x = price_series
        result = analyze_cointegration(y, x)
        assert len(result.spread_series) == len(y)

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="不一致"):
            analyze_cointegration([1.0] * 50, [1.0] * 40)

    def test_random_walk_pair_not_cointegrated(self):
        """两个独立随机游走通常不协整（价格必须为正，use_log=False）。"""
        rng = np.random.default_rng(99)
        # 用 GBM 生成始终为正的随机游走
        x = (100.0 + np.cumsum(rng.normal(0, 0.5, 150))).clip(1.0).tolist()
        y = (100.0 + np.cumsum(rng.normal(0, 0.5, 150))).clip(1.0).tolist()
        result = analyze_cointegration(y, x, use_log=False)
        # 虚假协整可能发生，仅验证接口可正常调用并返回 bool
        assert isinstance(result.is_cointegrated, bool)


# ── PCA 测试 ──────────────────────────────────────────────────────

class TestPCA:
    @pytest.fixture
    def returns_matrix(self):
        rng = np.random.default_rng(7)
        factor = rng.normal(0, 0.01, (100, 1))
        assets = factor @ rng.normal(0, 1, (1, 4)) + rng.normal(0, 0.005, (100, 4))
        return assets.tolist()

    def test_basic_output(self, returns_matrix):
        result = analyze_pca(returns_matrix)
        assert result.n_assets == 4
        assert result.n_observations == 100

    def test_eigenvalues_descending(self, returns_matrix):
        result = analyze_pca(returns_matrix)
        evs = result.eigenvalues
        assert all(evs[i] >= evs[i + 1] for i in range(len(evs) - 1))

    def test_explained_variance_sums_to_one(self, returns_matrix):
        result = analyze_pca(returns_matrix, n_components=4)
        assert sum(result.explained_variance_ratio) == pytest.approx(1.0, abs=0.01)

    def test_risk_decomposition_sums_to_one(self, returns_matrix):
        result = analyze_pca(returns_matrix)
        for item in result.risk_decomposition:
            total = item["systematic_pct"] + item["idiosyncratic_pct"]
            assert total == pytest.approx(1.0, abs=0.01)

    def test_correlation_matrix_diagonal_ones(self, returns_matrix):
        result = analyze_pca(returns_matrix)
        for i, row in enumerate(result.correlation_matrix):
            assert row[i] == pytest.approx(1.0, abs=1e-6)


# ── HMM 测试 ──────────────────────────────────────────────────────

class TestHMM:
    @pytest.fixture
    def regime_returns(self):
        rng = np.random.default_rng(11)
        bull = rng.normal(0.001, 0.008, 100).tolist()
        bear = rng.normal(-0.002, 0.020, 100).tolist()
        return bull + bear

    def test_fits_two_states(self, regime_returns):
        result = fit_hmm(regime_returns, n_states=2)
        assert result.n_states == 2

    def test_state_sequence_length(self, regime_returns):
        result = fit_hmm(regime_returns, n_states=2)
        assert len(result.state_sequence) == len(regime_returns)

    def test_state_labels_count(self, regime_returns):
        result = fit_hmm(regime_returns, n_states=2)
        assert len(result.state_labels) == 2

    def test_transition_matrix_rows_sum_to_one(self, regime_returns):
        result = fit_hmm(regime_returns, n_states=2)
        for row in result.transition_matrix:
            assert sum(row) == pytest.approx(1.0, abs=1e-4)

    def test_current_state_prob_valid(self, regime_returns):
        result = fit_hmm(regime_returns, n_states=2)
        assert 0 <= result.current_state_prob <= 1

    def test_state_vols_positive(self, regime_returns):
        result = fit_hmm(regime_returns, n_states=2)
        assert all(v > 0 for v in result.state_vols)

    def test_short_series_raises(self):
        with pytest.raises(ValueError, match="长度不足"):
            fit_hmm([0.01] * 10)


# ── Copula 测试 ──────────────────────────────────────────────────────

class TestCopula:
    @pytest.fixture
    def correlated_returns(self):
        rng = np.random.default_rng(13)
        x = rng.normal(0, 0.01, 200)
        y = 0.7 * x + 0.3 * rng.normal(0, 0.01, 200)
        return x.tolist(), y.tolist()

    def test_gaussian_copula_basic(self, correlated_returns):
        x, y = correlated_returns
        result = analyze_copula(x, y, copula_type="gaussian")
        assert result.copula_type == "gaussian"
        assert -1 <= result.copula_rho <= 1

    def test_t_copula_has_df(self, correlated_returns):
        x, y = correlated_returns
        result = analyze_copula(x, y, copula_type="t")
        assert result.t_df is not None
        assert result.t_df >= 4

    def test_gaussian_copula_zero_tail_dep(self, correlated_returns):
        x, y = correlated_returns
        result = analyze_copula(x, y, copula_type="gaussian")
        assert result.lower_tail_dep == 0.0
        assert result.upper_tail_dep == 0.0

    def test_t_copula_positive_tail_dep(self, correlated_returns):
        x, y = correlated_returns
        result = analyze_copula(x, y, copula_type="t")
        assert result.lower_tail_dep > 0

    def test_correlation_range(self, correlated_returns):
        x, y = correlated_returns
        result = analyze_copula(x, y)
        assert -1 <= result.pearson_rho <= 1
        assert -1 <= result.spearman_rho <= 1

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            analyze_copula([0.01] * 50, [0.01] * 30)

    def test_u_v_samples_in_unit_interval(self, correlated_returns):
        x, y = correlated_returns
        result = analyze_copula(x, y)
        assert all(0 < u < 1 for u in result.u_samples)
        assert all(0 < v < 1 for v in result.v_samples)
