"""
期权链 → 合约元数据映射（V4 Wave F-b / O5）

`options_provider.py` / `options_service.py` 已经能拉到美股期权链（含本地算的 Greeks）。
本模块只做一件事：把那份**行情**数据里属于**合约身份**的部分
（到期日、行权价、认购认沽）抽出来，转成统一的 `ContractSpec`。

明确不做（契约 §二 / §三）：
- 不新增数据源（仍然只有 yfinance 美股链）；
- 不做定价、不做希腊值 —— `quant/bsm.py` 已有 BSM，本期**不接**，
  Greeks 继续留在 `OptionContract` 上，`ContractSpec` 里一个都没有；
- 不做下单、不做保证金。

为什么要这一层：`OptionContract` 是「某一时刻某合约的报价」，会随行情变；
`ContractSpec` 是「这份合约是什么」，一辈子不变。两者混在一起，
后续任何要缓存/落库合约表的功能都得重新拆一遍。
"""

from __future__ import annotations

from app.data.models.asset_class import AssetClass
from app.data.models.contract import VALID_OPTION_RIGHTS, ContractSpec, OptionRight
from app.data.providers.options_models import OptionContract, OptionsChainResponse

#: 美股股票期权的标准合约乘数：1 张 = 100 股。
#: 迷你合约/调整后合约（拆股、并购造成的 non-standard deliverable）不是 100，
#: 但 yfinance 的链数据不给 contractSize 的可靠取值，故这里只给默认值、允许调用方覆盖。
US_EQUITY_OPTION_MULTIPLIER = 100.0

#: 美股期权报价的最小变动价位有分档（<$3 为 0.05，≥$3 为 0.10，Penny Pilot 品种为 0.01）。
#: 分档规则依赖交易所的 Penny Program 名单，本期不引入，统一取最细的 0.01。
DEFAULT_OPTION_TICK_SIZE = 0.01


def normalize_option_right(raw: str | None) -> OptionRight:
    """把数据源的方向字段规整成 "call" / "put"。"""
    text = (raw or "").strip().lower()
    if text in VALID_OPTION_RIGHTS:
        return text  # type: ignore[return-value]
    if text in {"c", "calls"}:
        return "call"
    if text in {"p", "puts"}:
        return "put"
    raise ValueError(f"无法识别的期权方向 {raw!r}，可选 {sorted(VALID_OPTION_RIGHTS)}")


def occ_symbol(underlying: str, contract: OptionContract) -> str:
    """
    按 OCC 21 位标准合成合约代码（数据源没给 contractSymbol 时的兜底）。

    格式：`ROOT(左对齐补空格到6位，此处改为不补) + YYMMDD + C/P + 行权价×1000 补零到8位`
    例：AAPL 2024-06-21 到期、行权价 190.5 的认购 → `AAPL240621C00190500`

    ⚠️ 刻意**不**在 root 后补空格：OCC 原始规范用空格补齐到 6 位，
    但那样的代码带空格，既不能当文件名（归档层的 symbol 白名单会拒绝），
    也不是 yfinance / 各家券商实际使用的形态 —— 它们用的都是不补空格的紧凑写法。
    """
    if contract.expiration is None:
        raise ValueError(f"{underlying}: 期权缺少到期日，无法合成 OCC 代码")
    right = normalize_option_right(contract.option_type)[0].upper()
    strike_milli = int(round(float(contract.strike) * 1000))
    return f"{underlying.upper()}{contract.expiration:%y%m%d}{right}{strike_milli:08d}"


def option_contract_to_spec(
    underlying: str,
    contract: OptionContract,
    *,
    multiplier: float = US_EQUITY_OPTION_MULTIPLIER,
    tick_size: float = DEFAULT_OPTION_TICK_SIZE,
) -> ContractSpec:
    """
    单份 `OptionContract` → `ContractSpec`。

    到期日缺失即报错：`ContractSpec` 对期权强制要求 expiry + strike + right，
    一份没有到期日的期权合约无法定价也无法交割，静默补一个默认值比报错危险得多。
    """
    if not underlying or not underlying.strip():
        raise ValueError("underlying 不能为空")
    if contract.expiration is None:
        raise ValueError(
            f"{underlying}: 期权 {contract.contract_symbol or '?'} 缺少到期日，无法构造 ContractSpec"
        )

    return ContractSpec(
        symbol=contract.contract_symbol or occ_symbol(underlying, contract),
        asset_class=AssetClass.OPTION,
        multiplier=multiplier,
        tick_size=tick_size,
        underlying=underlying.strip().upper(),
        expiry=contract.expiration,
        strike=float(contract.strike),
        option_right=normalize_option_right(contract.option_type),
    )


def chain_to_specs(
    chain: OptionsChainResponse,
    *,
    multiplier: float = US_EQUITY_OPTION_MULTIPLIER,
    tick_size: float = DEFAULT_OPTION_TICK_SIZE,
) -> list[ContractSpec]:
    """整条期权链 → `ContractSpec` 列表（calls 在前、puts 在后，各自保持原顺序）。"""
    return [
        option_contract_to_spec(
            chain.symbol, contract, multiplier=multiplier, tick_size=tick_size
        )
        for contract in (*chain.calls, *chain.puts)
    ]
