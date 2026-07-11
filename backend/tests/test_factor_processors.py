"""横截面因子处理器 + 处理流水线单元测试

覆盖防泄漏核心不变量：
- RobustZScoreNorm 只在 train 窗口 fit（fit_end 之后的数据不得影响拟合参数）
- CSRankNorm / CSZScoreNorm 的单标的、常数、全 NaN 截面退化行为
- DropnaLabel 在 for_infer=True 时被跳过
- ProcessingPipeline 拒绝把有状态处理器放进 infer_processors
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.quant.processing_pipeline import (
    ProcessingPipeline,
    ProcessorConfig,
    build_processor,
)
from app.quant.processors import (
    CSRankNorm,
    CSZScoreNorm,
    DropnaLabel,
    ProcessorError,
    RobustZScoreNorm,
)


# ── 公用构造器 ─────────────────────────────────────────────────

def _panel(rows: list[tuple[str, str, float]], col: str = "f") -> pd.DataFrame:
    """由 (datetime, instrument, value) 三元组构造单列面板。"""
    idx = pd.MultiIndex.from_tuples(
        [(d, i) for d, i, _ in rows], names=["datetime", "instrument"]
    )
    return pd.DataFrame({col: [v for *_, v in rows]}, index=idx)


def _make_labeled_panel(
    dates: list[str],
    instruments: list[str],
    seed: int = 7,
    label_nan_tail: int = 1,
) -> pd.DataFrame:
    """构造含 feature + label 列的面板；每个标的末尾若干 label 置 NaN。"""
    rng = np.random.default_rng(seed)
    idx = pd.MultiIndex.from_product(
        [dates, instruments], names=["datetime", "instrument"]
    )
    n = len(idx)
    df = pd.DataFrame(
        {
            "f": rng.normal(0.0, 1.0, n),
            "label": rng.normal(0.0, 0.02, n),
        },
        index=idx,
    ).sort_index()
    # 每个标的最后 label_nan_tail 个 datetime 的 label 置 NaN
    if label_nan_tail > 0:
        tail_dates = dates[-label_nan_tail:]
        mask = df.index.get_level_values("datetime").isin(tail_dates)
        df.loc[mask, "label"] = np.nan
    return df


# ── CSRankNorm ────────────────────────────────────────────────

class TestCSRankNorm:
    def test_single_instrument_cross_section_yields_zero(self) -> None:
        # Arrange: 每个 datetime 只有一个标的（截面无信息）
        panel = _panel([("2020-01-01", "A", 5.0), ("2020-01-02", "A", 9.0)])

        # Act
        out = CSRankNorm()(panel)

        # Assert: count<=1 被 mask 为 0
        assert out["f"].tolist() == [0.0, 0.0]

    def test_constant_cross_section_is_uniform(self) -> None:
        # Arrange: 同一 datetime 三个标的取值相同
        panel = _panel(
            [
                ("2020-01-01", "A", 5.0),
                ("2020-01-01", "B", 5.0),
                ("2020-01-01", "C", 5.0),
            ]
        )

        # Act
        out = CSRankNorm()(panel)

        # Assert: 常数截面 rank 相同 → 输出彼此相等（无标的被区分）
        vals = out["f"].tolist()
        assert vals[0] == pytest.approx(vals[1]) == pytest.approx(vals[2])

    def test_all_nan_cross_section_yields_zero(self) -> None:
        # Arrange: 整截面全 NaN（有效计数为 0 → mask 为 0）
        panel = _panel(
            [("2020-01-01", "A", np.nan), ("2020-01-01", "B", np.nan)]
        )

        # Act
        out = CSRankNorm()(panel)

        # Assert
        assert out["f"].tolist() == [0.0, 0.0]

    def test_does_not_mutate_input(self) -> None:
        # Arrange
        panel = _panel(
            [("2020-01-01", "A", 1.0), ("2020-01-01", "B", 2.0)]
        )
        before = panel.copy()

        # Act
        _ = CSRankNorm()(panel)

        # Assert: 不可变——输入未被原地修改
        pd.testing.assert_frame_equal(panel, before)


# ── CSZScoreNorm ──────────────────────────────────────────────

class TestCSZScoreNorm:
    def test_single_instrument_cross_section_yields_zero(self) -> None:
        # Arrange
        panel = _panel([("2020-01-01", "A", 5.0), ("2020-01-02", "A", 7.0)])

        # Act
        out = CSZScoreNorm()(panel)

        # Assert: 单标的 std=NaN→0，EPS 兜底 → 0
        assert out["f"].tolist() == pytest.approx([0.0, 0.0])

    def test_constant_cross_section_yields_zero(self) -> None:
        # Arrange
        panel = _panel(
            [
                ("2020-01-01", "A", 3.0),
                ("2020-01-01", "B", 3.0),
                ("2020-01-01", "C", 3.0),
            ]
        )

        # Act
        out = CSZScoreNorm()(panel)

        # Assert: std=0 → (x-mean)=0 → 0
        assert out["f"].tolist() == pytest.approx([0.0, 0.0, 0.0])

    def test_robust_constant_cross_section_yields_zero(self) -> None:
        # Arrange
        panel = _panel(
            [
                ("2020-01-01", "A", 3.0),
                ("2020-01-01", "B", 3.0),
                ("2020-01-01", "C", 3.0),
            ]
        )

        # Act
        out = CSZScoreNorm(method="robust")(panel)

        # Assert: MAD=0 → 0
        assert out["f"].tolist() == pytest.approx([0.0, 0.0, 0.0])

    def test_all_nan_cross_section_stays_nan(self) -> None:
        # Arrange
        panel = _panel(
            [("2020-01-01", "A", np.nan), ("2020-01-01", "B", np.nan)]
        )

        # Act
        out = CSZScoreNorm()(panel)

        # Assert: 全 NaN 截面无中心可算 → 保持 NaN
        assert out["f"].isna().all()

    def test_normalized_cross_section_has_zero_mean(self) -> None:
        # Arrange: 一个有区分度的截面
        panel = _panel(
            [
                ("2020-01-01", "A", 1.0),
                ("2020-01-01", "B", 2.0),
                ("2020-01-01", "C", 6.0),
            ]
        )

        # Act
        out = CSZScoreNorm()(panel)

        # Assert: 标准化后均值 ≈ 0
        assert out["f"].mean() == pytest.approx(0.0, abs=1e-9)

    def test_invalid_method_raises(self) -> None:
        with pytest.raises(ProcessorError):
            CSZScoreNorm(method="bogus")  # type: ignore[arg-type]


# ── RobustZScoreNorm 防泄漏不变量 ──────────────────────────────

class TestRobustZScoreNormNoLeak:
    def _train_test_panel(self) -> tuple[pd.DataFrame, list[str], list[str]]:
        train_dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
        test_dates = ["2020-01-04", "2020-01-05"]
        instruments = ["A", "B"]
        dates = train_dates + test_dates
        idx = pd.MultiIndex.from_product(
            [dates, instruments], names=["datetime", "instrument"]
        )
        # test 期刻意放入极端量级，若被误纳入拟合会显著改变 median/MAD
        values = []
        for d in dates:
            for _ in instruments:
                if d in test_dates:
                    values.append(1000.0)
                else:
                    values.append(float(len(values) % 5))
        panel = pd.DataFrame({"f": values}, index=idx).sort_index()
        return panel, train_dates, test_dates

    def test_fit_ignores_data_after_fit_end(self) -> None:
        # Arrange
        panel, train_dates, _ = self._train_test_panel()
        fit_start, fit_end = train_dates[0], train_dates[-1]

        # Act: 在含 test 期的全面板上 fit（应只用 train 切片）
        pipe = ProcessingPipeline(
            infer_processors=[],
            learn_processors=[RobustZScoreNorm(fields=["f"])],
        ).fit(panel, fit_start, fit_end)
        fitted_full = pipe.learn_processors[0]
        assert isinstance(fitted_full, RobustZScoreNorm)

        # 在仅含 train 期的截断面板上 fit
        train_only = panel[
            panel.index.get_level_values("datetime").isin(train_dates)
        ]
        fitted_train = RobustZScoreNorm(fields=["f"]).fit(train_only)

        # Assert: fit_end 之后的数据不改变拟合参数
        assert fitted_full._mean["f"] == pytest.approx(fitted_train._mean["f"])
        assert fitted_full._std["f"] == pytest.approx(fitted_train._std["f"])

    def test_fit_params_independent_of_appended_future_rows(self) -> None:
        # Arrange
        panel, train_dates, _ = self._train_test_panel()
        fit_start, fit_end = train_dates[0], train_dates[-1]

        # 截断到 fit_end（无未来数据）
        truncated = panel[
            panel.index.get_level_values("datetime") <= fit_end
        ]

        # Act: 有无 fit_end 之后的数据，分别 fit
        with_future = ProcessingPipeline(
            [], [RobustZScoreNorm(fields=["f"])]
        ).fit(panel, fit_start, fit_end).learn_processors[0]
        without_future = ProcessingPipeline(
            [], [RobustZScoreNorm(fields=["f"])]
        ).fit(truncated, fit_start, fit_end).learn_processors[0]

        # Assert: 参数一致
        assert with_future._mean["f"] == pytest.approx(without_future._mean["f"])
        assert with_future._std["f"] == pytest.approx(without_future._std["f"])

    def test_unfitted_call_raises(self) -> None:
        panel, _, _ = self._train_test_panel()
        with pytest.raises(ProcessorError):
            RobustZScoreNorm(fields=["f"])(panel)

    def test_is_stateful(self) -> None:
        assert RobustZScoreNorm().is_stateful is True

    def test_invalid_clip_bound_raises(self) -> None:
        with pytest.raises(ProcessorError):
            RobustZScoreNorm(clip_bound=0.0)


# ── DropnaLabel ───────────────────────────────────────────────

class TestDropnaLabel:
    def test_not_for_infer(self) -> None:
        assert DropnaLabel().is_for_infer() is False

    def test_drops_nan_label_rows_in_train(self) -> None:
        # Arrange
        panel = _make_labeled_panel(
            ["2020-01-01", "2020-01-02", "2020-01-03"],
            ["A", "B"],
            label_nan_tail=1,
        )

        # Act
        out = DropnaLabel()(panel)

        # Assert: 无 NaN label 行留存
        assert out["label"].notna().all()
        assert len(out) < len(panel)

    def test_skipped_when_for_infer(self) -> None:
        # Arrange
        panel = _make_labeled_panel(
            ["2020-01-01", "2020-01-02", "2020-01-03"],
            ["A", "B"],
            label_nan_tail=1,
        )
        pipe = ProcessingPipeline(
            infer_processors=[],
            learn_processors=[DropnaLabel()],
        )

        # Act
        infer_result = pipe.process(panel, for_infer=True)
        train_result = pipe.process(panel, for_infer=False)

        # Assert: 推理路径保留全部可交易行；训练路径丢弃 NaN label 行
        assert infer_result.n_rows_out == len(panel)
        assert infer_result.dropped_rows == 0
        assert train_result.dropped_rows > 0

    def test_empty_label_field_raises(self) -> None:
        with pytest.raises(ProcessorError):
            DropnaLabel(label_field="")


# ── ProcessingPipeline 不变量 ──────────────────────────────────

class TestProcessingPipelineInvariants:
    def test_rejects_stateful_processor_in_infer(self) -> None:
        # Arrange / Act / Assert: 有状态处理器不得置于 infer_processors
        with pytest.raises(ProcessorError, match="infer_processors"):
            ProcessingPipeline(
                infer_processors=[RobustZScoreNorm(fields=["f"])],
                learn_processors=[],
            )

    def test_rejects_non_infer_processor_in_infer(self) -> None:
        # Arrange / Act / Assert: is_for_infer=False（DropnaLabel）不得置于 infer
        with pytest.raises(ProcessorError):
            ProcessingPipeline(
                infer_processors=[DropnaLabel()],
                learn_processors=[],
            )

    def test_infer_processors_accept_stateless(self) -> None:
        # Arrange / Act: 无状态处理器可置于 infer
        pipe = ProcessingPipeline(
            infer_processors=[CSRankNorm(), CSZScoreNorm()],
            learn_processors=[],
        )

        # Assert
        assert len(pipe.infer_processors) == 2

    def test_build_processor_unknown_name_raises(self) -> None:
        with pytest.raises(ProcessorError):
            build_processor(ProcessorConfig(name="NoSuchProcessor"))

    def test_from_configs_wires_pipeline(self) -> None:
        # Arrange / Act
        pipe = ProcessingPipeline.from_configs(
            infer=[ProcessorConfig(name="CSRankNorm")],
            learn=[ProcessorConfig(name="RobustZScoreNorm", params={"fields": ["f"]})],
        )

        # Assert
        assert isinstance(pipe.infer_processors[0], CSRankNorm)
        assert isinstance(pipe.learn_processors[0], RobustZScoreNorm)
