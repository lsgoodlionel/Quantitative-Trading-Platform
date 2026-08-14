"""
自动因子循环 —— 异步编排（V3 · I2）

把 `auto_loop.py` 的纯计算内核接上外部世界：取数 → 切分 → 搜索 → 入库 → 复盘。

三条边界：

- **只入库，绝不上线**。产出写进 `LabStore`（一份 DATASET 产物），
  本模块从头到尾不认识 `promote_to_strategy`、不认识策略仓储、不认识实盘。
  晋级永远是人在界面上点的那一下 —— 一个没人看过的自动挖掘结果直接进入交易链路，
  是这类系统最典型的事故来源。
- **入库先于复盘**。复盘只是锦上添花，模型挂了不该让一轮几分钟的搜索白跑。
- **模型缺席不影响主流程**。未配置 provider 时跳过复盘，`llm_review` 留 None。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pandas as pd

from app.quant.lab.auto_loop import (
    DEFAULT_SEED_EXPRESSIONS,
    LoopConfig,
    LoopRound,
    new_round_id,
    run_search_round,
)
from app.quant.lab.sample_split import SampleSplit, split_by_is_end

logger = logging.getLogger(__name__)

#: 单标的最少 bar 数（与 factor_mining 端点一致）
MIN_BARS_PER_SYMBOL = 60
#: 横截面适应度所需最少标的数
MIN_SYMBOLS = 3
#: 未指定起始日时默认回看的年数
DEFAULT_LOOKBACK_YEARS = 2


class LoopDataError(ValueError):
    """取数或面板构建失败 —— universe 太小、bar 太少、日期非法。"""


async def execute_round(
    redis: Any,
    payload: dict,
    round_id: str | None = None,
) -> dict:
    """跑完一整轮，返回 `LoopRound.to_dict()`。异常向上抛，由任务层记进轮次记录。"""
    config, fitness_config, fetch = _parse_payload(payload)
    rid = round_id or new_round_id()

    bars_by_symbol = await _fetch_bars(config.universe, **fetch)
    split = _build_split(bars_by_symbol, config)

    seeds = tuple(payload.get("seeds") or DEFAULT_SEED_EXPRESSIONS)
    round_ = run_search_round(split, config, fitness_config, seeds=seeds, round_id=rid)

    round_ = await _persist_artifact(round_, market=fetch["market"])
    round_ = await _attach_review(redis, round_, config)
    return round_.to_dict()


# ── 取数 / 面板 ───────────────────────────────────────────────────

def _parse_payload(payload: dict) -> tuple[LoopConfig, Any, dict]:
    from app.quant.factor_fitness import FitnessConfig

    universe = tuple(str(s).strip().upper() for s in payload.get("universe", []) if str(s).strip())
    if len(universe) < MIN_SYMBOLS:
        raise LoopDataError(f"universe 至少需要 {MIN_SYMBOLS} 个标的，实得 {len(universe)}")

    try:
        is_end = date.fromisoformat(str(payload["is_end"]))
    except (KeyError, ValueError) as exc:
        raise LoopDataError(f"is_end 非法（需 YYYY-MM-DD）: {payload.get('is_end')!r}") from exc

    config = LoopConfig(
        universe=universe,
        is_end=is_end,
        generations=int(payload.get("generations", 5)),
        population=int(payload.get("population", 40)),
        top_k=int(payload.get("top_k", 5)),
        max_candidates_evaluated=int(payload.get("max_candidates_evaluated", 2000)),
        forward_period=int(payload.get("forward_period", 5)),
        max_depth=int(payload.get("max_depth", 4)),
        seed=int(payload.get("seed", 42)),
        use_cross_section=bool(payload.get("use_cross_section", False)),
    )
    fetch = {
        "market": str(payload.get("market", "US")),
        "frequency": str(payload.get("frequency", "1d")),
        "start": payload.get("start"),
        "end": payload.get("end"),
    }
    return config, FitnessConfig(), fetch


async def _fetch_bars(
    universe: tuple[str, ...],
    market: str,
    frequency: str,
    start: str | None,
    end: str | None,
) -> dict[str, list]:
    """在 worker 进程内自建 engine/session 取数（与 `app/tasks/validation.py` 同一约定）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.data.models import Frequency as FreqEnum
    from app.data.models import Market as MarketEnum
    from app.data.service import DataService

    end_date = date.fromisoformat(end) if end else date.today()
    start_date = (
        date.fromisoformat(start)
        if start
        else end_date - timedelta(days=365 * DEFAULT_LOOKBACK_YEARS)
    )
    try:
        market_enum = MarketEnum(market)
        freq_enum = FreqEnum(frequency)
    except ValueError as exc:
        raise LoopDataError(str(exc)) from exc

    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    bars_by_symbol: dict[str, list] = {}
    try:
        async with factory() as session:
            svc = DataService(session)
            for symbol in universe:
                try:
                    bars = await svc.get_bars(
                        symbol, market_enum, freq_enum, start_date, end_date
                    )
                except Exception:  # noqa: BLE001 — 单标的失败不作硬错误
                    logger.warning("自动因子循环取数失败，已跳过标的: %s", symbol)
                    continue
                if len(bars) >= MIN_BARS_PER_SYMBOL:
                    bars_by_symbol[symbol] = bars
    finally:
        await engine.dispose()

    if len(bars_by_symbol) < MIN_SYMBOLS:
        raise LoopDataError(
            f"有效标的不足（横截面搜索需 ≥ {MIN_SYMBOLS}，实得 {len(bars_by_symbol)}；"
            f"每标的需 ≥ {MIN_BARS_PER_SYMBOL} 根 bar）"
        )
    return bars_by_symbol


def _build_split(bars_by_symbol: dict[str, list], config: LoopConfig) -> SampleSplit:
    """由 bars 构建全量面板，再按 `is_end` 切成样本内 / 样本外。"""
    from app.quant.panel import _bars_to_ohlcv, attach_forward_label, bars_to_panel

    ohlcv_by_symbol = {sym: _bars_to_ohlcv(bars) for sym, bars in bars_by_symbol.items()}
    labeled = attach_forward_label(
        bars_to_panel(bars_by_symbol), config.forward_period, label_field="forward_return"
    )
    forward_return_panel = labeled[["forward_return"]].copy()
    liquidity_panel = (labeled["close"] * labeled["volume"]).to_frame("liquidity")

    return split_by_is_end(
        ohlcv_by_symbol,
        forward_return_panel,
        liquidity_panel,
        config.is_end,
        config.forward_period,
    )


# ── 入库（只入库，不上线）─────────────────────────────────────────

async def _persist_artifact(round_: LoopRound, market: str) -> LoopRound:
    """把存活因子写成一份 DATASET 产物。失败只记日志，不让一轮搜索白跑。"""
    if not round_.survivors:
        return round_

    from app.core.config import settings
    from app.quant.lab.content import FileSystemContentStore
    from app.quant.lab.metadata import PostgresArtifactMetaStore
    from app.quant.lab.store import LabStore

    frame = survivors_to_frame(round_)
    tags = {
        "source": "auto_factor_loop",
        "round_id": round_.round_id,
        "market": market,
        "is_end": round_.is_end,
        "hypotheses_tested": round_.hypotheses_tested,
        "truncated": round_.truncated,
        "out_of_sample_available": round_.out_of_sample_available,
        "overfit_suspects": sum(1 for c in round_.survivors if c.overfit_suspect),
        # 明示这份产物**没有**被注册成策略 —— 晋级是人工路径
        "promoted": "false",
    }

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            store = LabStore(PostgresArtifactMetaStore(session), FileSystemContentStore())
            # `PostgresArtifactMetaStore.save` 自己提交，这里不重复 commit
            meta = await store.save_dataset(
                f"auto_loop_{round_.round_id}", frame, tags=tags
            )
    except Exception as exc:  # noqa: BLE001 — 入库失败不该丢掉已经算出来的结果
        # 但也不能装作没发生：`artifact_error` 会一路传到前端，
        # 否则用户看到一轮「成功」却在产物库里找不到东西。
        logger.exception("自动因子循环产物入库失败: %s", round_.round_id)
        return replace(round_, artifact_id=None, artifact_error=str(exc))
    finally:
        await engine.dispose()

    return replace(round_, artifact_id=meta.artifact_id)


def survivors_to_frame(round_: LoopRound) -> pd.DataFrame:
    """存活因子 → 一张宽表。样本内/样本外两套指标**并排成列**，不合并。"""
    return pd.DataFrame([c.to_dict() for c in round_.survivors])


# ── 复盘（可缺席）─────────────────────────────────────────────────

async def _attach_review(redis: Any, round_: LoopRound, config: LoopConfig) -> LoopRound:
    """调 LLM 复盘并索取下一轮种子。没配 provider / 调用失败都只是少一段文字。"""
    from app.core.llm.base import LLMNotConfiguredError
    from app.core.llm.service import resolve_active
    from app.quant.lab.loop_llm import review_round

    try:
        resolved = await resolve_active(redis)
    except LLMNotConfiguredError:
        logger.info("未配置 LLM provider，本轮跳过复盘与提种子")
        return round_
    except Exception as exc:  # noqa: BLE001 — 解析 provider 失败同样只是没有复盘
        logger.warning("解析 LLM provider 失败，本轮跳过复盘：%s", exc)
        return replace(round_, llm_error=str(exc))

    outcome = await review_round(
        resolved.provider, round_, include_cross_section=config.use_cross_section
    )
    return replace(
        round_,
        llm_review=outcome.review,
        next_seeds=outcome.next_seeds,
        rejected_seed_count=outcome.rejected_seed_count,
        llm_error=outcome.error,
    )
