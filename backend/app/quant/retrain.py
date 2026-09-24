"""
自适应再训练 —— 编排（V4 · M6）

滚动窗口取数 → 建特征表 → 时序切分 → 训练新模型 → **新旧模型同段样本外对比**
→ 整体归档 → 发通知。漂移检测在 `app/quant/drift.py`，本模块只负责把它接上外部世界。

三条边界（契约 §3.1）
------------------------------------------------------------------
1. **绝不自动上线。** 产出只写进 `LabStore`，标签里写死 `activated=false`。
   本模块从头到尾不认识策略仓储、不认识实盘、没有任何「替换当前模型」的代码路径。
   人在界面上看过指标再决定 —— 与自动因子循环（I2）同一立场。
2. **新旧模型必须在同一段样本外数据上对比。** `_compare_models()` 拿同一个
   `holdout` 分别打分；旧模型缺席时 `previous_metrics=None` 并写明原因，
   而不是只报新模型的漂亮数字。
3. **失败不留半个模型。** 训练 + 评估 + 建分布快照全部完成后才发起唯一一次写入
   （`_persist`）。中途任何异常都在写入之前抛出 —— 旧模型一个字节都没动过，
   随后由任务层发失败通知。

⚠️ **漂移检测不触发重训。** `run_drift_check()` 只发通知并在结果里写死
`retrain_triggered=False`。市场剧变当天自动重训，学到的正是那天的噪声。

⚠️ **worker 进程拿不到 FastAPI 进程内的任何单例**（Wave C-b 的坑）。
本模块只依赖数据库与文件系统，自建 engine/session，不碰任何进程内状态。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from app.quant.drift import (
    DEFAULT_DI_THRESHOLD,
    DEFAULT_OUTLIER_RATIO_THRESHOLD,
    MAX_REFERENCE_ROWS,
    DriftAwareAlphaModel,
    DriftReport,
)

# `_build_features` 带下划线但就是 `ml_strategy` 里唯一的特征工程实现。
# 复制一份「一样的技术指标」出来，只会在两边慢慢漂成两套不可比的特征。
from app.quant.ml_strategy import FEATURE_NAMES, _build_features
from app.quant.models.template import (
    DEFAULT_LABEL_COLUMN,
    AlphaModelTemplate,
    evaluate_ic,
    split_features_label,
)

logger = logging.getLogger(__name__)

#: 支持的模型类型 → 构造器名。新增模型只改这张表。
MODEL_KINDS = ("lasso", "gradient_boosting")

#: 单标的最少 bar 数：特征里最长的滚动窗口是 20，再留出前瞻期与热身。
MIN_BARS_PER_SYMBOL = 120

#: 训练集 / 样本外各自的最少行数 —— 低于此的对比没有统计意义
MIN_TRAIN_ROWS = 60
MIN_HOLDOUT_ROWS = 20

#: 默认滚动窗口长度（自然日）
DEFAULT_LOOKBACK_DAYS = 730

#: 默认样本外占比（按时间取尾部）
DEFAULT_HOLDOUT_RATIO = 0.2

#: 年化因子的基数（日线）
TRADING_DAYS_PER_YEAR = 252


class RetrainError(RuntimeError):
    """再训练失败：取数不足、切分为空、模型类型非法。"""


# ── 配置 / 结果 ───────────────────────────────────────────────────

@dataclass(frozen=True)
class RetrainConfig:
    """一次再训练的全部输入。所有校验在构造时做完，失败得快一点。"""

    symbols: tuple[str, ...]
    market: str = "US"
    frequency: str = "1d"
    #: 滚动窗口：[end - lookback_days, end]
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    end: date | None = None
    forward_period: int = 5
    holdout_ratio: float = DEFAULT_HOLDOUT_RATIO
    model_kind: str = "lasso"
    label_column: str = DEFAULT_LABEL_COLUMN
    #: 上一版模型的产物 ID —— 用于同段样本外对比；缺省表示这是第一版
    previous_artifact_id: str | None = None
    max_reference_rows: int = MAX_REFERENCE_ROWS

    def __post_init__(self) -> None:
        if not self.symbols:
            raise RetrainError("symbols 不能为空")
        if self.model_kind not in MODEL_KINDS:
            raise RetrainError(f"未知模型类型 {self.model_kind!r}，可选: {list(MODEL_KINDS)}")
        if self.lookback_days < 30:
            raise RetrainError(f"lookback_days 至少 30 天，实得 {self.lookback_days}")
        if self.forward_period < 1:
            raise RetrainError(f"forward_period 必须 ≥ 1，实得 {self.forward_period}")
        if not 0.05 <= self.holdout_ratio <= 0.5:
            raise RetrainError(f"holdout_ratio 需落在 [0.05, 0.5]，实得 {self.holdout_ratio}")

    @property
    def end_date(self) -> date:
        return self.end or date.today()

    @property
    def start_date(self) -> date:
        return self.end_date - timedelta(days=self.lookback_days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": list(self.symbols),
            "market": self.market,
            "frequency": self.frequency,
            "lookback_days": self.lookback_days,
            "start": self.start_date.isoformat(),
            "end": self.end_date.isoformat(),
            "forward_period": self.forward_period,
            "holdout_ratio": self.holdout_ratio,
            "model_kind": self.model_kind,
            "previous_artifact_id": self.previous_artifact_id,
        }


@dataclass(frozen=True)
class RetrainOutcome:
    """一次再训练的产出。`activated` 恒为 False —— 上线是人工动作。"""

    model_kind: str
    window_start: str
    window_end: str
    n_train_rows: int
    n_holdout_rows: int
    #: 新模型在样本外的 ic / rank_ic / sharpe
    new_metrics: dict[str, float]
    #: 旧模型在**同一段**样本外的同一组指标；没有旧模型时为 None
    previous_metrics: dict[str, float] | None
    previous_artifact_id: str | None
    #: 为什么没有旧模型指标（未提供 / 加载失败 / 特征列对不上）
    previous_unavailable_reason: str | None
    comparison: dict[str, Any]
    baseline_summary: dict[str, Any]
    artifact_id: str | None = None
    activated: bool = False
    #: 附带跑一次漂移检测：新窗口的样本外相对**新模型训练集**的偏离度
    holdout_drift: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_kind": self.model_kind,
            "window": {"start": self.window_start, "end": self.window_end},
            "n_train_rows": self.n_train_rows,
            "n_holdout_rows": self.n_holdout_rows,
            "new_metrics": dict(self.new_metrics),
            "previous_metrics": (
                dict(self.previous_metrics) if self.previous_metrics is not None else None
            ),
            "previous_artifact_id": self.previous_artifact_id,
            "previous_unavailable_reason": self.previous_unavailable_reason,
            "comparison": dict(self.comparison),
            "baseline_summary": dict(self.baseline_summary),
            "artifact_id": self.artifact_id,
            "activated": self.activated,
            "activation_note": (
                "重训产出只入库，不会自动替换线上模型；确认指标后请人工上线。"
            ),
            "holdout_drift": self.holdout_drift,
        }


@dataclass(frozen=True)
class TrainedBundle:
    """训练 + 评估的中间结果，写入产物库前的完整快照（还没碰过任何存储）。"""

    model: DriftAwareAlphaModel
    outcome: RetrainOutcome
    tags: dict[str, str] = field(default_factory=dict)


# ── 纯计算：建表 / 切分 / 评估 / 训练 ─────────────────────────────

def build_feature_frame(bars_by_symbol: dict[str, list]) -> pd.DataFrame:
    """
    每标的 bar 序列 → `(datetime, instrument)` 索引的**纯特征**宽表（无标签）。

    特征沿用 `ml_strategy.FEATURE_NAMES` 那一套技术指标 —— 复用既有实现，
    不为再训练另起一套特征工程（那会让新旧模型的可比性无从谈起）。

    漂移检测走这条路径而不是 `build_training_frame`：后者会因标签为 NaN 丢掉
    每个标的**最后 forward_period 根 bar**，而那几根正是最需要看一眼的最新数据。
    """
    from app.quant.panel import bars_to_panel

    usable = {sym: bars for sym, bars in bars_by_symbol.items() if len(bars) >= MIN_BARS_PER_SYMBOL}
    if not usable:
        raise RetrainError(f"没有任何标的达到 {MIN_BARS_PER_SYMBOL} 根 bar 的下限")

    panel = bars_to_panel(usable, feature_fn=_build_features)
    frame = panel[list(FEATURE_NAMES)].replace([np.inf, -np.inf], np.nan).dropna()
    if frame.empty:
        raise RetrainError("特征表清洗后为空 —— 窗口太短或数据缺口太多")
    return frame.sort_index()


def build_training_frame(
    bars_by_symbol: dict[str, list],
    forward_period: int,
    label_column: str = DEFAULT_LABEL_COLUMN,
) -> pd.DataFrame:
    """在特征表上追加前瞻收益标签，得到训练用的「特征 + 标签」宽表。"""
    from app.quant.panel import attach_forward_label, bars_to_panel

    if forward_period < 1:
        raise RetrainError(f"forward_period 必须 ≥ 1，实得 {forward_period}")
    usable = {sym: bars for sym, bars in bars_by_symbol.items() if len(bars) >= MIN_BARS_PER_SYMBOL}
    if not usable:
        raise RetrainError(f"没有任何标的达到 {MIN_BARS_PER_SYMBOL} 根 bar 的下限")

    panel = bars_to_panel(usable, feature_fn=_build_features)
    labeled = attach_forward_label(panel, forward_period, label_field=label_column)
    frame = labeled[[*FEATURE_NAMES, label_column]].replace([np.inf, -np.inf], np.nan).dropna()
    if frame.empty:
        raise RetrainError("特征表清洗后为空 —— 窗口太短或数据缺口太多")
    return frame.sort_index()


def split_train_holdout(
    frame: pd.DataFrame, holdout_ratio: float = DEFAULT_HOLDOUT_RATIO
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    **按时间**切出训练集与样本外，尾部归样本外。

    绝不随机切：随机切会把同一天的横截面同时放进两边，样本外的「未来」信息
    经由相邻行泄回训练集，对比出来的指标一律好看且一律无效。
    """
    if frame.empty:
        raise RetrainError("特征表为空，无法切分")
    dates = frame.index.get_level_values("datetime")
    unique_dates = np.unique(np.asarray(dates))
    if len(unique_dates) < 2:
        raise RetrainError("特征表只覆盖一个日期，无法切出样本外")

    n_holdout_dates = max(1, int(round(len(unique_dates) * holdout_ratio)))
    cut_index = len(unique_dates) - n_holdout_dates
    if cut_index < 1:
        raise RetrainError("样本外占比过大，训练集会被切空")
    cut_date = unique_dates[cut_index]

    train = frame[np.asarray(dates) < cut_date]
    holdout = frame[np.asarray(dates) >= cut_date]
    if len(train) < MIN_TRAIN_ROWS:
        raise RetrainError(f"训练集不足 {MIN_TRAIN_ROWS} 行（实得 {len(train)}）")
    if len(holdout) < MIN_HOLDOUT_ROWS:
        raise RetrainError(f"样本外不足 {MIN_HOLDOUT_ROWS} 行（实得 {len(holdout)}）")
    return train, holdout


def signal_sharpe(
    prediction: pd.Series, label: pd.Series, forward_period: int
) -> float:
    """
    把预测值当方向信号，算样本外夏普。

    `pnl[t] = sign(pred[t]) * label[t]`，按日期取横截面均值后年化。

    ⚠️ 标签是**重叠的** `forward_period` 日前瞻收益，相邻日的 pnl 高度相关，
    因此这个夏普系统性偏乐观，**不能**当成可交易的收益预期。它在这里的唯一
    用途是「新旧模型在同一段数据、同一套算法下谁更高」—— 偏差对双方一致。
    """
    aligned = pd.concat([prediction.rename("pred"), label.rename("label")], axis=1).dropna()
    if aligned.empty:
        return 0.0
    pnl = np.sign(aligned["pred"]) * aligned["label"]
    if isinstance(aligned.index, pd.MultiIndex) and "datetime" in (aligned.index.names or []):
        pnl = pnl.groupby(level="datetime").mean()
    if len(pnl) < 2:
        return 0.0
    std = float(pnl.std(ddof=1))
    if not np.isfinite(std) or std <= 0:
        return 0.0
    annualization = float(np.sqrt(TRADING_DAYS_PER_YEAR / max(forward_period, 1)))
    return round(float(pnl.mean()) / std * annualization, 6)


def evaluate_out_of_sample(
    model: AlphaModelTemplate,
    holdout: pd.DataFrame,
    label_column: str,
    forward_period: int,
) -> dict[str, float]:
    """样本外打分：IC / RankIC / 夏普。新旧模型走的是同一个函数、同一份 holdout。"""
    features, label = split_features_label(holdout, label_column)
    prediction = model.predict(features)
    metrics = dict(evaluate_ic(prediction, label))
    metrics["sharpe"] = signal_sharpe(prediction, label, forward_period)
    return metrics


def build_model(model_kind: str, label_column: str = DEFAULT_LABEL_COLUMN) -> AlphaModelTemplate:
    """按类型名构造一个未训练的模型。"""
    if model_kind == "lasso":
        from app.quant.models.linear import LassoAlphaModel

        return LassoAlphaModel(label_column=label_column)
    if model_kind == "gradient_boosting":
        from app.quant.models.boosting import GradientBoostingAlphaModel

        return GradientBoostingAlphaModel(label_column=label_column)
    raise RetrainError(f"未知模型类型 {model_kind!r}，可选: {list(MODEL_KINDS)}")


def train_and_compare(
    frame: pd.DataFrame,
    config: RetrainConfig,
    previous_model: AlphaModelTemplate | None = None,
    previous_unavailable_reason: str | None = None,
) -> TrainedBundle:
    """
    纯计算的核心：切分 → 训练 → 新旧同段对比 → 组装产出。

    **不碰任何存储**。这正是「重训失败不留下半个模型」的实现方式 ——
    所有可能失败的步骤都排在唯一一次写入之前。
    """
    train, holdout = split_train_holdout(frame, config.holdout_ratio)

    model = DriftAwareAlphaModel(
        build_model(config.model_kind, config.label_column),
        max_reference_rows=config.max_reference_rows,
    )
    model.fit(train)

    new_metrics = evaluate_out_of_sample(
        model, holdout, config.label_column, config.forward_period
    )
    previous_metrics, reason = _score_previous(
        previous_model, holdout, config, previous_unavailable_reason
    )
    holdout_features, _ = split_features_label(holdout, config.label_column)
    drift = model.check_drift(holdout_features)

    outcome = RetrainOutcome(
        model_kind=config.model_kind,
        window_start=config.start_date.isoformat(),
        window_end=config.end_date.isoformat(),
        n_train_rows=int(len(train)),
        n_holdout_rows=int(len(holdout)),
        new_metrics=new_metrics,
        previous_metrics=previous_metrics,
        previous_artifact_id=config.previous_artifact_id,
        previous_unavailable_reason=reason,
        comparison=compare_metrics(new_metrics, previous_metrics),
        baseline_summary=model.baseline.summary(),
        holdout_drift=drift.to_dict(),
    )
    return TrainedBundle(model=model, outcome=outcome, tags=_artifact_tags(config, outcome))


def compare_metrics(
    new_metrics: dict[str, float], previous_metrics: dict[str, float] | None
) -> dict[str, Any]:
    """
    新旧并排的差值。**不给「该不该换」的结论** —— 只给差值和一句口径说明。

    `is_better` 三态：没有旧模型时为 None，而不是默认 True。
    """
    if previous_metrics is None:
        return {
            "has_previous": False,
            "is_better": None,
            "note": "没有可对比的旧模型，本次指标无法说明「变好了」还是「变差了」。",
        }
    deltas = {
        f"{key}_delta": round(
            float(new_metrics.get(key, 0.0)) - float(previous_metrics.get(key, 0.0)), 6
        )
        for key in ("ic", "rank_ic", "sharpe")
    }
    return {
        "has_previous": True,
        **deltas,
        # 三项里两项以上不降才算「不差于」—— 单看 IC 会被一次噪声反转
        "is_better": bool(sum(1 for v in deltas.values() if v > 0) >= 2),
        "note": "差值为「新 − 旧」，均在同一段样本外、同一套指标下计算。是否上线由人决定。",
    }


# ── IO 编排 ───────────────────────────────────────────────────────

async def run_retrain(config: RetrainConfig, *, store: Any | None = None) -> RetrainOutcome:
    """
    跑完一次再训练：取数 → 训练 + 对比 → **整体写入** → 返回产出。

    `store` 供测试注入；生产路径在 worker 进程内自建 engine/session。
    任何异常都向上抛，由任务层记录并发失败通知 —— 此时旧模型没有被动过。
    """
    bars = await _fetch_bars(config)
    frame = build_training_frame(bars, config.forward_period, config.label_column)

    previous_model, reason = await _load_previous_model(config, store)
    bundle = train_and_compare(frame, config, previous_model, reason)

    artifact_id = await _persist(bundle, config, store)
    return replace(bundle.outcome, artifact_id=artifact_id)


async def run_drift_check(
    artifact_id: str,
    config: RetrainConfig,
    *,
    store: Any | None = None,
    di_threshold: float = DEFAULT_DI_THRESHOLD,
    outlier_ratio_threshold: float = DEFAULT_OUTLIER_RATIO_THRESHOLD,
) -> dict[str, Any]:
    """
    用已归档模型的训练集分布，判断最近这段数据是否漂了。

    ⚠️ **只报告，不动作**：结果里的 `retrain_triggered` 恒为 False。
    要不要重训是人看完报告之后的决定 —— 见模块 docstring。
    """
    async with _resolve_store(store) as lab:
        model = await lab.load_model(artifact_id)
    if not isinstance(model, DriftAwareAlphaModel):
        raise RetrainError(
            f"产物 {artifact_id} 不带训练集分布快照（类型 {type(model).__name__}），"
            "无法判断漂移 —— 只有 M6 之后归档的模型才有。"
        )

    bars = await _fetch_bars(config)
    # 用无标签的特征表：带标签会因 NaN 丢掉每个标的最后 forward_period 根 bar，
    # 而最新那几根恰恰是判漂移最该看的
    features = build_feature_frame(bars)
    report: DriftReport = model.check_drift(
        features,
        di_threshold=di_threshold,
        outlier_ratio_threshold=outlier_ratio_threshold,
    )
    return {
        "artifact_id": artifact_id,
        "window": {"start": config.start_date.isoformat(), "end": config.end_date.isoformat()},
        "report": report.to_dict(),
        # 写死 False 并给出理由：这个字段的存在本身就是给后来者的护栏
        "retrain_triggered": False,
        "retrain_note": (
            "检测到漂移只发通知，不自动重训 —— 市场剧变当天重训，学到的正是那天的噪声。"
        ),
    }


# ── 内部：取数 / 加载 / 写入 ──────────────────────────────────────

async def _fetch_bars(config: RetrainConfig) -> dict[str, list]:
    """在 worker 进程内自建 engine/session 取数（与 `app/tasks/validation.py` 同一约定）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.data.models import Frequency as FreqEnum
    from app.data.models import Market as MarketEnum
    from app.data.service import DataService

    try:
        market_enum = MarketEnum(config.market)
        freq_enum = FreqEnum(config.frequency)
    except ValueError as exc:
        raise RetrainError(str(exc)) from exc

    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    bars_by_symbol: dict[str, list] = {}
    try:
        async with factory() as session:
            svc = DataService(session)
            for symbol in config.symbols:
                try:
                    bars = await svc.get_bars(
                        symbol, market_enum, freq_enum, config.start_date, config.end_date
                    )
                except Exception:  # noqa: BLE001 — 单标的失败不作硬错误
                    logger.warning("再训练取数失败，已跳过标的: %s", symbol)
                    continue
                if len(bars) >= MIN_BARS_PER_SYMBOL:
                    bars_by_symbol[symbol] = bars
    finally:
        await engine.dispose()

    if not bars_by_symbol:
        raise RetrainError(
            f"窗口 {config.start_date}~{config.end_date} 内没有任何标的达到 "
            f"{MIN_BARS_PER_SYMBOL} 根 bar 的下限"
        )
    return bars_by_symbol


async def _load_previous_model(
    config: RetrainConfig, store: Any | None
) -> tuple[AlphaModelTemplate | None, str | None]:
    """
    加载上一版模型用于对比。**加载失败不让整次重训失败** —— 但要如实写明原因，
    否则用户看到的是一份「没有对比」的报告却不知道为什么。
    """
    if not config.previous_artifact_id:
        return None, "未指定 previous_artifact_id，本次没有对比基准"
    try:
        async with _resolve_store(store) as lab:
            model = await lab.load_model(config.previous_artifact_id)
    except Exception as exc:  # noqa: BLE001 — 见 docstring
        logger.warning("加载旧模型失败: %s", config.previous_artifact_id)
        return None, f"旧模型加载失败: {exc}"
    if not isinstance(model, AlphaModelTemplate):
        return None, f"旧模型不是 AlphaModelTemplate（实得 {type(model).__name__}）"
    return model, None


def _score_previous(
    previous_model: AlphaModelTemplate | None,
    holdout: pd.DataFrame,
    config: RetrainConfig,
    reason: str | None,
) -> tuple[dict[str, float] | None, str | None]:
    """旧模型在同一段样本外打分。打分失败（特征列变了）同样只降级为「无对比」。"""
    if previous_model is None:
        return None, reason or "没有可用的旧模型"
    try:
        return (
            evaluate_out_of_sample(
                previous_model, holdout, config.label_column, config.forward_period
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("旧模型样本外打分失败: %s", exc)
        return None, f"旧模型打分失败（特征列可能已变更）: {exc}"


def _artifact_tags(config: RetrainConfig, outcome: RetrainOutcome) -> dict[str, str]:
    """产物标签。`activated=false` 是写给未来的自己看的：这东西没上线。"""
    return {
        "source": "adaptive_retrain",
        "model_kind": config.model_kind,
        "market": config.market,
        "window_start": outcome.window_start,
        "window_end": outcome.window_end,
        "symbols": ",".join(config.symbols),
        "oos_ic": str(outcome.new_metrics.get("ic", 0.0)),
        "oos_rank_ic": str(outcome.new_metrics.get("rank_ic", 0.0)),
        "oos_sharpe": str(outcome.new_metrics.get("sharpe", 0.0)),
        "previous_artifact_id": config.previous_artifact_id or "",
        "has_previous_metrics": str(outcome.previous_metrics is not None).lower(),
        # 与 I2 同一立场：自动产物只入库，不进交易链路
        "activated": "false",
        "promoted": "false",
    }


async def _persist(bundle: TrainedBundle, config: RetrainConfig, store: Any | None) -> str:
    """唯一一次写入。到这一步为止训练与评估都已完成，不会留下半个模型。"""
    name = f"retrain_{config.model_kind}_{bundle.outcome.window_end}"
    async with _resolve_store(store) as lab:
        meta = await lab.save_model(name, bundle.model, tags=bundle.tags)
    return str(meta.artifact_id)


@asynccontextmanager
async def _resolve_store(store: Any | None) -> AsyncIterator[Any]:
    """注入的 store 直接用；否则自建 engine/session 并在退出时释放。"""
    if store is not None:
        yield store
        return

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.quant.lab.content import FileSystemContentStore
    from app.quant.lab.metadata import PostgresArtifactMetaStore
    from app.quant.lab.store import LabStore

    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield LabStore(PostgresArtifactMetaStore(session), FileSystemContentStore())
    finally:
        await engine.dispose()
