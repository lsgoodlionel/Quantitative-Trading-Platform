# V4 Wave F-b 契约：多资产扩展预留（O5）

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **O5** · Agent-Fb
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）**逐字节不变且全绿**。
> 本项动的是 `data/models` 这种全局结构，最容易误伤基线 —— 一旦变色立刻停下来查。

---

## 零、「预留」是这一项的关键词

蓝图写的是「多资产扩展**预留**」，不是「支持期货期权交易」。
两者差一个量级，别做成后者。

本期目标：**让数据模型能表达非股票资产，且不破坏现有一切。**
交易链路（下单/撮合/风控）本期一律不动。

---

## 一、要做的事

### 1.1 资产类别

```python
class AssetClass(str, Enum):
    EQUITY = "equity"        # 股票（默认）
    FUTURES = "futures"
    OPTION = "option"
    FX = "fx"
    CRYPTO = "crypto"
```

`Bar` 增加 `asset_class: AssetClass = AssetClass.EQUITY`。

⚠️ **必须带默认值且默认是 EQUITY。** `Bar` 是 frozen dataclass，
被回归基线的 1364 笔成交钉着 —— 加一个无默认值的字段会让所有既有构造点报错，
加一个默认值不是 EQUITY 的字段会让既有行为漂移。

⚠️ **`asset_class` 不参与 `__post_init__` 的任何校验、不参与撮合分支。**
本期它只是一个**标签**。任何「若是期货则走另一条撮合路径」的逻辑都属于下一期。

### 1.2 合约元数据

```python
@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    asset_class: AssetClass
    multiplier: float = 1.0          # 合约乘数（股票=1）
    tick_size: float = 0.01
    # 期货
    underlying: str | None = None
    expiry: date | None = None
    # 期权
    strike: float | None = None
    option_right: Literal["call", "put"] | None = None
```

放在新文件 `app/data/models/contract.py`，**不要塞进 `bar.py`**。

### 1.3 连续合约拼接（只做最小可用）

期货主力合约换月会在价格序列上留下跳空。提供一个纯函数：

```python
def stitch_continuous(
    contracts: Sequence[tuple[ContractSpec, list[Bar]]],
    method: Literal["raw", "back_adjust"] = "back_adjust",
) -> list[Bar]: ...
```

⚠️ **`back_adjust` 与 `raw` 的语义差异必须写进 docstring 并有测试钉住**：
后向复权保证价差连续（可用于计算收益），但**历史价格不是当时的真实成交价**。
这与项目既有的复权口径讨论一脉相承（见 `data/adjustments.py`）——
两者不要混用，也不要在同一张图上画。

⚠️ **不做**主力合约的自动判定（成交量/持仓量规则），由调用方给出合约顺序。
自动判定规则各交易所不同，做错了整条价格序列都是错的。

---

## 二、期权链

`options_provider.py` 已存在（仅美股）。本期只做一件事：
把它返回的数据映射成 `ContractSpec`，**不新增数据源、不做定价、不做希腊值**
（`bsm.py` 已有 BSM，本期不接）。

---

## 三、绝对不做

- 期货/期权的**下单与撮合**（`broker.py` 一行不改）
- 保证金与逐日盯市
- 跨品种保证金优惠
- 主力合约自动判定（见 §1.3）
- 把 `asset_class` 接进任何撮合/风控分支（见 §1.1）

> 这些每一条都是独立的一期工作量。本期做完，下一期才有地基。

---

## 四、验收

```
1. tests/regression 146 用例全绿，且 git diff tests/regression/ 为空
   ← 这是本项最重要的验收项：动了 Bar 还能不变色，才说明是真「预留」
2. tests/test_asset_class.py:
   - 不传 asset_class 构造的 Bar，asset_class == EQUITY
   - 既有全部 Bar 构造点无需修改（反射断言字段有默认值）
   - asset_class 不参与 __post_init__ 校验（传任意合法值都不报错）
3. tests/test_contract_spec.py:
   - 期货 spec 必须有 expiry，期权 spec 必须有 strike + right（缺失即报错）
   - 股票 spec 的 multiplier 默认 1.0
4. tests/test_continuous_futures.py:
   - back_adjust 后相邻合约衔接处**无跳空**（价差连续）
   - raw 保留原始价格（衔接处有跳空，且测试断言它确实有）
   - 单合约输入原样返回
   - 空输入返回空列表而非报错
5. 归档层（data/archive/columns.py）能往返 asset_class 而不丢失
6. ruff check app tests → All checks passed! · pytest -q 全绿
```
