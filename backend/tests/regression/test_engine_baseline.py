"""引擎逐笔回归基线测试。

作用：冻结「现有单标的回测引擎在固定数据上的行为」。
Wave K 把引擎升级为组合引擎时，单标的路径必须逐笔通过这份基线；
真要改行为，就显式重跑 `python -m tests.regression.generate_baseline` 并审阅 diff。

覆盖：16 个 preset 策略 × 3 个市场（各自的手续费/滑点/T+1 分支）× 3 种价格情景。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.data.models import Market
from tests.regression.fixtures import case_id, iter_cases
from tests.regression.snapshot import build_snapshot

GOLDEN_PATH = Path(__file__).parent / "golden" / "engine_baseline.json"

_CASES = iter_cases()


@pytest.fixture(scope="module")
def golden() -> dict:
    if not GOLDEN_PATH.exists():
        pytest.fail(
            f"缺少基线文件 {GOLDEN_PATH}。"
            f"请运行 `python -m tests.regression.generate_baseline` 生成。"
        )
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["cases"]


def test_golden_covers_all_cases(golden: dict) -> None:
    """基线必须与当前用例枚举一一对应——新增策略时会在此暴露。"""
    expected = {case_id(n, m, r) for n, m, r in _CASES}
    actual = set(golden)

    assert not (expected - actual), (
        f"基线缺少 {len(expected - actual)} 个用例（新增策略后需重新生成）："
        f"{sorted(expected - actual)[:5]}"
    )
    assert not (actual - expected), (
        f"基线存在 {len(actual - expected)} 个已废弃用例："
        f"{sorted(actual - expected)[:5]}"
    )


def test_golden_is_meaningful(golden: dict) -> None:
    """基线必须真的锁住了成交行为，而不是一堆空跑用例。"""
    total_fills = sum(c["fills_count"] for c in golden.values())
    assert total_fills > 500, f"基线只锁住 {total_fills} 笔成交，覆盖不足"

    strategies_with_fills = {
        cid.split("|")[0] for cid, c in golden.items() if c["fills_count"] > 0
    }
    all_strategies = {n for n, _, _ in _CASES}
    assert strategies_with_fills == all_strategies, (
        f"以下策略在所有用例中均无成交，基线对其无约束力："
        f"{sorted(all_strategies - strategies_with_fills)}"
    )


@pytest.mark.parametrize(
    ("strategy_name", "market", "regime"),
    _CASES,
    ids=[case_id(n, m, r) for n, m, r in _CASES],
)
def test_engine_matches_baseline(
    strategy_name: str, market: Market, regime, golden: dict
) -> None:
    cid = case_id(strategy_name, market, regime)
    expected = golden[cid]
    actual = build_snapshot(strategy_name, market, regime)

    # 先比标量：失败信息更可读
    assert actual["fills_count"] == expected["fills_count"], (
        f"{cid} 成交笔数漂移：{expected['fills_count']} → {actual['fills_count']}"
    )
    assert actual["final_value"] == expected["final_value"], (
        f"{cid} 期末净值漂移：{expected['final_value']} → {actual['final_value']}"
    )

    # 再逐笔比对
    for i, (exp_fill, act_fill) in enumerate(zip(expected["fills"], actual["fills"], strict=True)):
        assert act_fill == exp_fill, f"{cid} 第 {i} 笔成交漂移：{exp_fill} → {act_fill}"

    # 最后比指标与净值锚点
    for key, exp_value in expected["metrics"].items():
        assert actual["metrics"][key] == exp_value, (
            f"{cid} 指标 {key} 漂移：{exp_value} → {actual['metrics'][key]}"
        )
    for key in ("equity_len", "equity_head", "equity_mid", "equity_tail"):
        assert actual[key] == expected[key], (
            f"{cid} {key} 漂移：{expected[key]} → {actual[key]}"
        )
