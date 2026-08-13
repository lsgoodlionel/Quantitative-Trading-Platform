"""Insight — Alpha 与组合构建之间的统一信号中间表示（Wave K-d / K5）

翻译自 Lean 的 `Common/Algorithm/Framework/Alphas/Insight.cs`（Apache-2.0），
按本期需要做了裁剪：保留身份（`insight_id`）、分组（`group_id`）、方向、有效期、
幅度/置信度/权重与来源；**不做** `Score` / `ReferenceValue` / `EstimatedValue` /
`InsightType`（这些服务于 N4 事后归因，本期不实现）。

为什么 `group_id` 必须现在就有：配对/价差类 Alpha 的多空两腿必须原子执行。
缺了它，组合构建可能执行了多头腿、却因权重或价格约束丢掉空头腿，
留下一条与策略意图完全相反的**裸露单边敞口** —— 那是错误，不是少个功能。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum

#: 传统单标的策略自己管平仓，其观点在被反向信号取代前一直有效。
#: 用一个「足够长」的有效期表达，避免到处特判 None。
NEVER_EXPIRES = timedelta(days=365 * 100)


class InsightDirection(int, Enum):
    """观点方向。取值本身即权重符号，可直接参与乘法。"""

    DOWN = -1
    FLAT = 0
    UP = 1


@dataclass(frozen=True)
class Insight:
    """
    一条「对某标的在某段时间内的看法」。

    frozen：观点一旦产生就不该被下游改写。分组用 `group_insights()` 返回新实例。
    """

    symbol: str
    direction: InsightDirection
    period: timedelta                    # 观点有效期
    generated_at: datetime
    magnitude: float | None = None       # 预期收益率
    confidence: float | None = None      # 0..1
    weight: float | None = None          # 建议权重（因子打分可直接给）
    source: str = ""                     # 产出者名，用于归因
    tag: str = ""
    #: 稳定标识：现在加是一行，将来补是一次数据迁移（N4 进出场归因要用）
    insight_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    #: 同组观点必须原子执行（配对交易的多空两腿）
    group_id: str | None = None

    @property
    def close_time(self) -> datetime:
        """观点失效时刻。"""
        return self.generated_at + self.period

    def is_expired_at(self, now: datetime) -> bool:
        """相对给定时钟判断是否过期。

        **只提供这一个带参版本，不提供 `is_expired` 属性** —— 契约初稿写的是无参属性，
        但那在回测里恒为真：`generated_at` 是历史时刻，跟墙上时钟比，每条观点一生成
        就过期。而一个叫 `is_expired` 的属性正是最容易被顺手用错的东西。
        实盘要用墙上时钟就显式传 `datetime.now(tz=...)`，也更好测。
        """
        return self.close_time < now


def group_insights(*insights: Insight) -> list[Insight]:
    """
    把若干 Insight 标成同一组，返回带 `group_id` 的**新实例**（原实例不变）。

    已属于其他组的 Insight 直接报错：静默改组会把既有配对关系悄悄拆散，
    而拆散配对正是这个字段要防的事故。
    """
    if not insights:
        raise ValueError("group_insights 至少需要一条观点")

    already = [i for i in insights if i.group_id is not None]
    if already:
        raise ValueError(
            f"这些观点已属于其他分组，不能重复分组: "
            f"{[(i.symbol, i.group_id) for i in already]}"
        )

    group_id = str(uuid.uuid4())[:12]
    return [replace(i, group_id=group_id) for i in insights]


__all__ = ["NEVER_EXPIRES", "Insight", "InsightDirection", "group_insights"]
