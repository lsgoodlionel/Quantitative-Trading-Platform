# Wave M-a 契约：本地数据归档（M1）+ 市值/成分股宇宙（M7）

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **M1 / M7** · Agent-Ma
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 一、M1 本地数据归档

### 1.1 现状

全库无 `app/data/archive/`，也无任何 parquet 相关代码。回测每次都从
在线源或 TimescaleDB 取数：在线源慢且受限流，反复调参时每次等待都是纯浪费。

### 1.2 设计

```
app/data/archive/
    base.py          # ArchiveHandler 抽象 + ArchiveKey(symbol, market, frequency)
    parquet.py       # ParquetArchive：读/写/列出/删除
    gaps.py          # 缺口检测与增量补齐计划
```

```python
@dataclass(frozen=True)
class ArchiveKey:
    symbol: str
    market: Market
    frequency: Frequency

class ArchiveHandler(ABC):
    def read(self, key, start: date, end: date) -> list[Bar]: ...
    def write(self, key, bars: list[Bar]) -> int: ...      # 返回实际写入条数
    def list_keys(self) -> list[ArchiveKey]: ...
    def coverage(self, key) -> tuple[date, date] | None: ...
    def delete(self, key) -> bool: ...
```

**存储布局**：`{archive_root}/{market}/{frequency}/{symbol}.parquet`，
路径从 `settings` 读，默认 `./data/archive`。

⚠️ **`symbol` 会进文件路径，必须做路径穿越防护**：拒绝含 `/`、`\`、`..`、
空字节的代码，而不是靠「代码里不会有这些字符」的假设 —— symbol 来自 API 入参。

### 1.3 缺口检测与增量补齐

```python
@dataclass(frozen=True)
class Gap:
    start: date
    end: date

def find_gaps(
    have: list[date], want_start: date, want_end: date,
    calendar: TradingCalendar | None = None,
) -> list[Gap]: ...
```

**与 K7 交易日历的关系**：给了日历就按交易日算缺口（周末不算缺），
没给就按自然日的连续段。**默认不给** —— 引入日历会让缺口判定依赖假日表的准确性，
而假日表本身是尽力而为的。

### 1.4 接入回测取数

`DataService.get_bars` 增加**归档优先**：

```
归档命中且覆盖完整 → 直接返回
部分命中          → 归档 + 在线补缺口，并把补到的写回归档
完全未命中        → 走原路径，成功后写回归档
```

⚠️ **必须可关闭**（`settings.archive_enabled`，默认 **False**）。
理由：归档是缓存，缓存出错的表现是「回测结果莫名其妙变了」，
这是最难排查的一类问题。先让它默认关，用户显式开启。

### 1.5 API 与任务

```
POST /api/v1/data/archive/download   { symbols, market, frequency, start, end } → 提交 Celery 任务
GET  /api/v1/data/archive            列出已归档的 key 与覆盖区间
DELETE /api/v1/data/archive/{...}
```

Celery 任务沿用 `app/tasks/data.py` 已有的
「自建 async engine + `asyncio.run()` 桥接」约定，**不要新建基础设施**。

---

## 二、M7 市值/成分股宇宙

### 2.1 现状

`app/data/screener.py` 与 `app/data/pairlist.py` 已有筛选与 pairlist 插件体系。
缺的是「按市值排名取前 N」与「按指数成分股取宇宙」两个数据源。

### 2.2 设计

```python
# app/data/universe/market_cap.py
async def top_by_market_cap(
    market: Market, top_n: int, *, exclude: set[str] | None = None
) -> list[UniverseItem]: ...

# app/data/universe/index_components.py
async def index_components(index_code: str, on: date | None = None) -> list[str]: ...
```

**数据源**：US 走 yfinance、A 股走 AkShare —— 两者都已在 `app/data/providers/`。
**不新增依赖**。

### 2.3 三个必须说清的点

1. **成分股是有历史的**。`on=None` 取最新，给了日期则取该日的成分。
   拿不到历史成分时**明确报错而不是静默返回最新** —— 用历史回测配上今天的成分股
   是典型的幸存者偏差，静默降级会产出看起来很美的假回测。
   本期若数据源只能给最新成分，就只暴露最新，并在 docstring 写明这一限制。

2. **市值榜单要缓存**。逐个标的拉市值在 500 只规模下会非常慢且触发限流。
   缓存进 Redis，TTL 建议 6 小时，并在返回体里带 `as_of` 时间戳。

3. **接进 pairlist 而非另起一套**。`app/data/pairlist/` 已有插件链式组合的体系，
   新增 `MarketCapPairList` / `IndexComponentPairList` 两个插件，
   而不是在 screener 里再写一份筛选逻辑。

---

## 三、验收

```
1. tests/regression 146 用例全绿
2. tests/test_data_archive.py:
   - 写入 → 读回，bar 逐笔一致（含 time/ohlcv/volume 全字段）
   - 路径穿越：symbol 含 "../" / "/" / 空字节一律拒绝（各一个用例）
   - coverage 返回实际区间；空归档返回 None
   - find_gaps：完全命中 / 头部缺 / 尾部缺 / 中间缺 / 全缺 五种情形
   - archive_enabled=False 时 DataService 行为与归档上线前**完全一致**
3. tests/test_universe.py:
   - 市值榜单命中缓存不重复请求（用 mock 断言调用次数）
   - 成分股取历史日期而数据源只有最新时 → 报错，不静默返回最新
   - 两个 pairlist 插件能与既有插件链式组合
4. ruff check app tests → All checks passed! · pytest -q 全绿
```

## 四、不做

- 归档的压缩/分区调优（先跑通，性能另议）
- 跨机器共享归档（本地目录即可）
- 指数成分股的完整历史回溯（数据源限制，本期只做能拿到的）
