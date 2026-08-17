"""
资产类别标签（V4 Wave F-b / O5）

**这是一个标签，不是一条交易链路。**

本期只让数据模型「能表达」非股票资产：`Bar.asset_class` 记录这根 K 线属于哪类资产，
`ContractSpec.asset_class` 记录合约元数据属于哪类资产。仅此而已。

刻意**不**做的事（每一条都是独立的一期工作量）：
- 不参与 `Bar.__post_init__` 的任何校验 —— 任何合法枚举值都不会让 Bar 构造失败；
- 不参与撮合/风控分支 —— 回测撮合器（`engine/backtest/broker.py`）一行不改，
  没有「若是期货则走另一条路径」的逻辑；
- 不带保证金、逐日盯市、合约乘数结算等任何衍生品语义。

⚠️ 单独放一个模块而不是塞进 `bar.py`：`bar.py` 与 `contract.py` 都要用它，
放在任一侧都会让另一侧产生一条不必要的反向依赖。
"""

from __future__ import annotations

from enum import Enum


class AssetClass(str, Enum):
    """资产类别。继承 `str` 以便直接 JSON 序列化，与 `Market` / `Frequency` 一致。"""

    EQUITY = "equity"      # 股票（默认；既有全部数据都属于这一类）
    FUTURES = "futures"    # 期货
    OPTION = "option"      # 期权
    FX = "fx"              # 外汇
    CRYPTO = "crypto"      # 加密货币


#: 默认资产类别。**不要改**：既有归档、既有 Bar 构造点、回归基线全部依赖
#: 「不写 asset_class 就是股票」这条语义。
DEFAULT_ASSET_CLASS = AssetClass.EQUITY
