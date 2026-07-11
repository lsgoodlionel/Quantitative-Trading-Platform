# Interface Contract — Wave-1 C6 / C7: Rich Backtest Reporting + Pyfolio Tearsheet

> **Status:** Contract (design only). No implementation in this document.
> **Scope:** QuantBot backtest reporting extensions.
> **Features:** C6 (rich reporting: tag/exit breakdowns, periodic stats, streaks, expectancy, drawdown-periods, 60+ risk ratios) and C7 (pyfolio-style tearsheet + trade-level round-trip analytics).
> **Backwards compatibility:** ADDITIVE ONLY. Every existing field on `BacktestResponse` and `BacktestMetrics` is preserved unchanged. New data lives in five new **optional** top-level sections so existing consumers (Overview tab, Optimize, MonteCarlo) keep working with zero changes.

---

## 0. Design principles

1. **No duplication.** The current `metrics` block (see §5) is the source of truth for the ~21 headline metrics. New sections MUST NOT re-emit `sharpe_ratio`, `total_return_pct`, `max_drawdown_pct`, etc. The new `tag_metrics.risk_ratios` block adds ONLY metrics not already in `metrics`.
2. **Round-trips are the unit of trade analytics** (C7). The current engine emits a flat `fills[]` list keyed by BUY/SELL; a round-trip pairs entries with exits. All C7 trade stats derive from reconstructed round-trips, NOT from raw sell fills.
3. **Sections are independently nullable.** If there are `< 2` trades or `< 2` periods, the corresponding section returns an empty list / null so the frontend can render an empty state per-tab.
4. **Immutability & small files** per repo coding-style: each new concern is its own module (200–400 lines target).

---

## 1. New backend files (`backend/app/engine/backtest/`)

| File | Responsibility | Key public functions |
|------|----------------|----------------------|
| `roundtrips.py` | Reconstruct round-trip trades from the flat `fills[]` list via FIFO lot matching. Produces the canonical `RoundTrip` list that C7 analytics + tag breakdowns consume. | `build_round_trips(fills: list[dict], bars_index: pd.DatetimeIndex \| None = None) -> list[RoundTrip]` |
| `trade_analytics.py` | C7 round-trip aggregate stats (total/won/lost, win %, avg win/loss, largest win/loss, streaks, holding periods, long/short splits, expectancy). | `compute_trade_analytics(trips: list[RoundTrip], starting_balance: float) -> TradeAnalytics` |
| `periodic_stats.py` | C6 daily / weekly / monthly / weekday breakdown tables (profit, wins/draws/losses, profit_factor per bucket) + best/worst period. | `compute_periodic_stats(trips: list[RoundTrip], equity_curve: pd.Series) -> PeriodicStats` |
| `rolling_stats.py` | C7 tearsheet rolling series: returns series, rolling Sharpe, rolling volatility, rolling beta (vs buy-hold benchmark), exposure, cumulative turnover. | `compute_rolling_stats(equity_curve, returns, benchmark_returns, fills, window) -> RollingStats` |
| `drawdown_periods.py` | C6 drawdown-periods table: every peak→trough→recovery episode with depth, length, recovery length, underwater days. | `compute_drawdown_periods(equity_curve: pd.Series, top_n: int = 10) -> list[DrawdownPeriod]` |
| `tag_metrics.py` | C6 per-`entry_tag` / per-`exit_reason` breakdown + the extended 60+ risk-ratio suite (omega, serenity, ulcer, CVaR, max-underwater, avg-holding, win-rate by long/short, etc.) not already in `metrics.py`. | `compute_tag_metrics(trips, returns, equity_curve, starting_balance) -> TagMetrics` |
| `report_sections.py` | Thin orchestrator: calls the six modules above and returns the five serialized dicts to splice into `build_report`. Keeps `report.py` small. | `build_extended_sections(equity_curve, returns, fills, starting_balance, benchmark_returns) -> dict` |

**Reference sources (definitions only, do not copy code):**
- `refs/jesse/jesse/services/metrics.py` — serenity, ulcer, CVaR, omega, max-underwater, holding periods, win-rate long/short, streaks.
- `refs/backtrader/backtrader/analyzers/tradeanalyzer.py` — won/lost/long/short PnL splits, streak longest, bar-length (holding) stats.
- `refs/freqtrade/.../optimize_reports.py` — `generate_tag_metrics`, `generate_periodic_breakdown_stats`, `calc_streak`, `_calculate_stats_for_period`, drawdown episodes.
- pyfolio — rolling Sharpe/beta/vol, exposure, drawdown table, round-trip stats.

### 1.1 Fill schema prerequisite (engine change, additive)

Current `_fill_to_dict` (engine.py) emits: `order_id, symbol, market, side, qty, price, commission, filled_at, realized_pnl`. To enable tag breakdowns and long/short splits, add two OPTIONAL fields, defaulted so nothing breaks if a strategy omits them:

```python
"entry_tag":   fill.entry_tag   or None,   # e.g. "ma_cross", "rsi_oversold"
"exit_reason": fill.exit_reason or None,   # e.g. "signal", "stop_loss", "take_profit", "eod"
"direction":   "long" | "short",           # position direction the fill closes/opens
```

If a strategy provides no tags, `entry_tag`/`exit_reason` fall back to the constants `DEFAULT_ENTRY_TAG = "untagged"` and `DEFAULT_EXIT_REASON = "signal"`, and `direction` defaults to `"long"` (current engine is long-only spot). Tag grouping then collapses to a single `"untagged"` / `"signal"` bucket — still valid.

---

## 2. Extending the backtest result response

### 2.1 Envelope

`BacktestResponse` (in `api/v1/endpoints/backtests.py`) and `report.py`'s `build_report` return gain five new keys. All are optional with safe empty defaults:

```python
class BacktestResponse(BaseModel):
    # ── existing (UNCHANGED) ──
    backtest_id: str
    strategy_name: str
    symbol: str
    market: str
    start_date: str
    end_date: str
    initial_cash: float
    final_value: float
    metrics: BacktestMetricsResponse          # unchanged, 21 fields (§5)
    equity_curve: list[EquityPoint]
    drawdown_series: list[dict]
    monthly_returns: dict
    pnl_distribution: list[dict]
    fills: list[dict]
    generated_at: str

    # ── NEW (Wave-1 C6/C7), all optional ──
    trade_analytics: TradeAnalytics | None = None      # C7
    periodic_stats:  PeriodicStats  | None = None      # C6
    rolling_stats:   RollingStats   | None = None      # C7
    drawdown_periods: list[DrawdownPeriod] = Field(default_factory=list)  # C6
    tag_metrics:     TagMetrics     | None = None      # C6
```

`report.py::build_report` gains `report.update(build_extended_sections(...))`; the endpoint passes the five keys straight through with `report.get(...)`.

### 2.2 `TradeAnalytics` (C7 — round-trip stats)

```python
class RoundTripRow(BaseModel):
    trip_id: int
    entry_time: str                 # ISO
    exit_time: str                  # ISO
    direction: str                  # "long" | "short"
    entry_tag: str
    exit_reason: str
    qty: float
    entry_price: float
    exit_price: float
    pnl: float                      # net realized PnL (currency, after commission)
    pnl_pct: float                  # pnl / entry_notional * 100
    commission: float
    holding_bars: int               # bars held (0 if bar index unavailable)
    holding_days: float             # (exit_time - entry_time) in days

class TradeAnalytics(BaseModel):
    total_trades: int               # count of closed round-trips
    won: int
    lost: int
    breakeven: int                  # pnl == 0
    win_rate_pct: float             # won / total * 100
    # PnL aggregates (currency)
    gross_profit: float             # sum(pnl where pnl>0)
    gross_loss: float               # sum(pnl where pnl<0)  (negative)
    net_profit: float               # gross_profit + gross_loss
    avg_win: float
    avg_loss: float                 # negative
    ratio_avg_win_loss: float       # avg_win / |avg_loss|
    largest_win: float
    largest_loss: float             # negative
    avg_trade_pnl: float            # net_profit / total_trades
    # streaks
    longest_win_streak: int
    longest_loss_streak: int
    current_streak: int             # signed: +N wins / -N losses at end
    # holding
    avg_holding_days: float
    avg_winning_holding_days: float
    avg_losing_holding_days: float
    max_holding_days: float
    min_holding_days: float
    # long/short split
    long_count: int
    short_count: int
    long_pct: float
    short_pct: float
    win_rate_long_pct: float
    win_rate_short_pct: float
    long_pnl: float
    short_pnl: float
    # activity
    avg_trades_per_day: float
    avg_trades_per_week: float
    avg_trades_per_month: float
    # full table (capped at 500 rows, same policy as fills[:500])
    round_trips: list[RoundTripRow] = Field(default_factory=list)
```

### 2.3 `PeriodicStats` (C6 — periodic breakdowns)

```python
class PeriodBucket(BaseModel):
    label: str                      # "2026-07-11" | "2026-W28" | "2026-07" | "Monday"
    date_ts: int                    # epoch ms (0 for weekday buckets, use dayofweek 0-6)
    profit_abs: float               # sum of round-trip pnl closing in the bucket
    profit_pct: float               # profit_abs / starting_balance * 100
    wins: int
    draws: int
    losses: int
    trades: int
    profit_factor: float            # winning_profit / |losing_profit|, 0 if no losses

class PeriodicStats(BaseModel):
    daily:   list[PeriodBucket] = Field(default_factory=list)
    weekly:  list[PeriodBucket] = Field(default_factory=list)   # resample 1W-MON
    monthly: list[PeriodBucket] = Field(default_factory=list)   # resample 1ME
    weekday: list[PeriodBucket] = Field(default_factory=list)   # Mon..Sun aggregate
    best_day:   PeriodBucket | None = None
    worst_day:  PeriodBucket | None = None
    best_month: PeriodBucket | None = None
    worst_month: PeriodBucket | None = None
    winning_days: int
    losing_days: int
    winning_weeks: int
    losing_weeks: int
    winning_months: int
    losing_months: int
```

### 2.4 `RollingStats` (C7 — tearsheet series)

```python
class SeriesPoint(BaseModel):
    time: str                       # ISO
    value: float

class RollingStats(BaseModel):
    window: int                     # rolling window in trading days (default 63 ≈ 3 months)
    returns_series:  list[SeriesPoint] = Field(default_factory=list)   # daily % returns
    cum_returns:     list[SeriesPoint] = Field(default_factory=list)   # cumulative growth of $1
    rolling_sharpe:  list[SeriesPoint] = Field(default_factory=list)
    rolling_volatility: list[SeriesPoint] = Field(default_factory=list)  # annualized %
    rolling_beta:    list[SeriesPoint] = Field(default_factory=list)   # vs buy-hold benchmark
    exposure_series: list[SeriesPoint] = Field(default_factory=list)   # invested / equity, 0..1
    turnover_series: list[SeriesPoint] = Field(default_factory=list)   # cumulative traded notional / equity
    # headline scalars for the tearsheet header row
    avg_exposure_pct: float
    total_turnover: float           # sum(traded notional) / avg equity
    beta: float                     # full-sample beta vs benchmark
    alpha_annual_pct: float         # full-sample annualized alpha
```

All series use the same ≤1000-point downsampling policy already in `report._series_to_points`.

### 2.5 `DrawdownPeriod` (C6 — drawdown-periods table)

```python
class DrawdownPeriod(BaseModel):
    rank: int                       # 1 = deepest
    peak_date: str                  # ISO — equity high before the drop
    valley_date: str                # ISO — lowest point
    recovery_date: str | None       # ISO — new-high date, null if still underwater at end
    depth_pct: float                # (valley - peak) / peak * 100 (negative)
    length_days: int                # peak → recovery (or end if unrecovered)
    drawdown_days: int              # peak → valley
    recovery_days: int | None       # valley → recovery, null if unrecovered
    max_underwater_days: int        # longest stretch below peak within the episode
```

Returned as a top-level `drawdown_periods: list[DrawdownPeriod]` (top-N, default 10, sorted by depth).

### 2.6 `TagMetrics` (C6 — tag breakdown + extended risk suite)

```python
class TagRow(BaseModel):
    key: str                        # entry_tag value / exit_reason value / "TOTAL"
    trades: int
    wins: int
    draws: int
    losses: int
    win_rate_pct: float
    profit_abs: float
    profit_pct: float               # profit_abs / starting_balance * 100
    profit_factor: float
    avg_pnl: float
    avg_holding_days: float

class RiskRatios(BaseModel):
    # ── ONLY metrics NOT already in `metrics` block (§5) ──
    cagr_pct: float
    ulcer_index: float
    serenity_index: float
    cvar_95_pct: float              # conditional value-at-risk (expected shortfall)
    value_at_risk_95_pct: float
    max_underwater_days: int
    recovery_factor: float          # net_profit / |max_drawdown_abs|
    payoff_ratio: float             # avg_win / |avg_loss|
    tail_ratio: float               # p95(returns) / |p5(returns)|
    common_sense_ratio: float       # tail_ratio * profit_factor
    kelly_criterion: float
    skew: float
    kurtosis: float
    downside_deviation_pct: float
    gain_to_pain_ratio: float       # sum(returns) / |sum(negative returns)|
    avg_holding_period_days: float
    avg_up_month_pct: float
    avg_down_month_pct: float
    win_rate_long_pct: float
    win_rate_short_pct: float
    profit_factor_long: float
    profit_factor_short: float
    best_trade_pct: float
    worst_trade_pct: float
    # ... (see §4.5 for the full grouped catalogue of 60+; each is one float/int field here)

class TagMetrics(BaseModel):
    by_entry_tag:   list[TagRow] = Field(default_factory=list)   # includes trailing "TOTAL"
    by_exit_reason: list[TagRow] = Field(default_factory=list)   # includes trailing "TOTAL"
    risk_ratios:    RiskRatios
```

---

## 3. Frontend TypeScript interfaces + new tabs

### 3.1 New types (`frontend/src/types/index.ts`, additive)

Mirror the Pydantic models above 1:1 (camelCase preserved as snake_case to match JSON):

```ts
export interface RoundTripRow { trip_id: number; entry_time: string; exit_time: string;
  direction: "long" | "short"; entry_tag: string; exit_reason: string; qty: number;
  entry_price: number; exit_price: number; pnl: number; pnl_pct: number;
  commission: number; holding_bars: number; holding_days: number }

export interface TradeAnalytics { total_trades: number; won: number; lost: number; breakeven: number;
  win_rate_pct: number; gross_profit: number; gross_loss: number; net_profit: number;
  avg_win: number; avg_loss: number; ratio_avg_win_loss: number; largest_win: number; largest_loss: number;
  avg_trade_pnl: number; longest_win_streak: number; longest_loss_streak: number; current_streak: number;
  avg_holding_days: number; avg_winning_holding_days: number; avg_losing_holding_days: number;
  max_holding_days: number; min_holding_days: number; long_count: number; short_count: number;
  long_pct: number; short_pct: number; win_rate_long_pct: number; win_rate_short_pct: number;
  long_pnl: number; short_pnl: number; avg_trades_per_day: number; avg_trades_per_week: number;
  avg_trades_per_month: number; round_trips: RoundTripRow[] }

export interface PeriodBucket { label: string; date_ts: number; profit_abs: number; profit_pct: number;
  wins: number; draws: number; losses: number; trades: number; profit_factor: number }
export interface PeriodicStats { daily: PeriodBucket[]; weekly: PeriodBucket[]; monthly: PeriodBucket[];
  weekday: PeriodBucket[]; best_day: PeriodBucket | null; worst_day: PeriodBucket | null;
  best_month: PeriodBucket | null; worst_month: PeriodBucket | null;
  winning_days: number; losing_days: number; winning_weeks: number; losing_weeks: number;
  winning_months: number; losing_months: number }

export interface SeriesPoint { time: string; value: number }
export interface RollingStats { window: number; returns_series: SeriesPoint[]; cum_returns: SeriesPoint[];
  rolling_sharpe: SeriesPoint[]; rolling_volatility: SeriesPoint[]; rolling_beta: SeriesPoint[];
  exposure_series: SeriesPoint[]; turnover_series: SeriesPoint[]; avg_exposure_pct: number;
  total_turnover: number; beta: number; alpha_annual_pct: number }

export interface DrawdownPeriod { rank: number; peak_date: string; valley_date: string;
  recovery_date: string | null; depth_pct: number; length_days: number; drawdown_days: number;
  recovery_days: number | null; max_underwater_days: number }

export interface TagRow { key: string; trades: number; wins: number; draws: number; losses: number;
  win_rate_pct: number; profit_abs: number; profit_pct: number; profit_factor: number;
  avg_pnl: number; avg_holding_days: number }
export interface RiskRatios { cagr_pct: number; ulcer_index: number; serenity_index: number;
  cvar_95_pct: number; value_at_risk_95_pct: number; max_underwater_days: number; recovery_factor: number;
  payoff_ratio: number; tail_ratio: number; common_sense_ratio: number; kelly_criterion: number;
  skew: number; kurtosis: number; downside_deviation_pct: number; gain_to_pain_ratio: number;
  avg_holding_period_days: number; avg_up_month_pct: number; avg_down_month_pct: number;
  win_rate_long_pct: number; win_rate_short_pct: number; profit_factor_long: number;
  profit_factor_short: number; best_trade_pct: number; worst_trade_pct: number }
export interface TagMetrics { by_entry_tag: TagRow[]; by_exit_reason: TagRow[]; risk_ratios: RiskRatios }
```

Extend `BacktestResult` (additive, all optional so old responses still type-check):

```ts
export interface BacktestResult {
  /* …existing… */
  trade_analytics?: TradeAnalytics
  periodic_stats?: PeriodicStats
  rolling_stats?: RollingStats
  drawdown_periods?: DrawdownPeriod[]
  tag_metrics?: TagMetrics
}
```

`useBacktest.ts` needs **no change** — it already returns the raw `BacktestResult`; the new fields ride along.

### 3.2 New result sub-tabs in `BacktestResultPanel` (`pages/Backtest.tsx`)

The existing `resultTab` union `"overview" | "drawdown" | "monthly" | "trades"` gains two entries:

```ts
type ResultTab = "overview" | "tearsheet" | "trade_analytics" | "drawdown" | "monthly" | "trades"
```

`RESULT_TABS` array gains `{ key: "tearsheet", label: "Tearsheet" }` and `{ key: "trade_analytics", label: "交易分析" }`. Each tab guards on its optional section and renders an `EmptyState` when absent (e.g. `< 2` trades).

#### Tab: **Tearsheet** (C7) — layout description

1. **Header stat strip** (reuse `MetricCard` grid, 4-up): Beta, Annual Alpha, Avg Exposure %, Total Turnover — pulled from `rolling_stats` scalars.
2. **Cumulative returns chart** — full-width `AreaChart` from `rolling_stats.cum_returns` (growth of $1), with a faint buy-hold benchmark overlay line.
3. **Rolling Sharpe** — line chart from `rolling_stats.rolling_sharpe`, horizontal reference line at y=1.
4. **Two-up row:** Rolling Volatility (%) area chart + Rolling Beta line chart.
5. **Two-up row:** Exposure area (0–100%) + cumulative Turnover line.
6. **Drawdown-periods table** (C6, from `drawdown_periods`): columns Rank / Peak / Valley / Recovery / Depth % / DD Days / Recovery Days / Underwater Days. Depth cell colored red; unrecovered rows show a "进行中" badge in Recovery.

New chart components live under `frontend/src/components/charts/` (e.g. `RollingLine.tsx`, `RollingArea.tsx`) — reuse the existing recharts styling tokens (`#58a6ff`, `#161b22`, etc.). Layout only; no implementation here.

#### Tab: **Trade Analytics** (C7) — layout description

1. **Summary MetricCard grid (4×N):** Total / Won / Lost, Win %, Ratio Avg W/L, Payoff, Largest Win, Largest Loss, Longest Win Streak, Longest Loss Streak, Avg Holding Days, Trades/Week — all from `trade_analytics`.
2. **Long vs Short panel:** side-by-side cards showing count, win-rate, and PnL for long and short, plus a small stacked bar (long/short share).
3. **Extended risk-ratio grid** (C6, from `tag_metrics.risk_ratios`): compact 2-column definition list grouped by heading — Risk-adjusted, Drawdown/Tail, Distribution, Trade quality (see §4.5 groups). Each row: label + value + ⓘ tooltip carrying the formula.
4. **Entry-tag breakdown table** (`tag_metrics.by_entry_tag`): Tag / Trades / Win % / Profit / PF / Avg PnL / Avg Hold, TOTAL row pinned at bottom, sorted by Profit desc.
5. **Exit-reason breakdown table** (`tag_metrics.by_exit_reason`): same columns keyed by exit reason.
6. **Round-trip table** (`trade_analytics.round_trips`, first 100 rows): Entry→Exit time, Direction, Tag, Exit reason, Qty, Entry/Exit price, PnL, PnL %, Holding days. Reuses the existing fills-table styling.
7. **Periodic strip** (C6, `periodic_stats`): three mini bar charts (daily/weekly/monthly profit) + best/worst period callouts.

---

## 4. Behavior notes — formulas & round-trip construction

### 4.0 Round-trip construction from fills (`roundtrips.py`)

The engine emits a chronological `fills[]` of BUY/SELL events with per-fill `realized_pnl` (computed by the broker at close). A **round-trip** is one open→close cycle:

- **FIFO lot matching.** Maintain a per-symbol queue of open lots. A BUY (long) or SELL-short opens/extends lots; the opposite side consumes lots FIFO. When a lot's remaining qty hits 0, that consumed slice becomes one closed round-trip.
- `entry_time / entry_price` = weighted values of the opening lot(s); `exit_time / exit_price` = the closing fill.
- `direction` = `"long"` if opened by BUY else `"short"` (current engine is long-only, so effectively all long until short support lands).
- `pnl` = sum of the broker's `realized_pnl` on the closing fills for that lot (already net of commission) — **reuse broker PnL, do not recompute**, to stay consistent with `metrics.expectancy`.
- `pnl_pct` = `pnl / (entry_price * qty) * 100`.
- `holding_bars` = index distance in the bars series (if `bars_index` supplied); `holding_days` = `(exit_time - entry_time).days` (fractional).
- `entry_tag` / `exit_reason` copied from the opening / closing fill (defaults per §1.1).

**Invariant:** `sum(trip.pnl for trips) == sum(sell_fill.realized_pnl)` (current `total_trades`/`expectancy` basis). This keeps C7 reconciled with the existing `metrics` block.

### 4.1 Risk-adjusted ratios

| Metric | Formula |
|--------|---------|
| Sharpe *(existing)* | `annual_return / annual_volatility`, rf=0. |
| Sortino *(existing)* | `annual_return / (downside_std * √periods)`, downside = returns<0. |
| Calmar *(existing)* | `annual_return / |max_drawdown|`. |
| Omega *(existing)* | `Σ max(r−θ,0) / Σ max(θ−r,0)`, θ=0. |
| **CAGR** | `(equity_end/equity_start)^(365/days) − 1`. (Calendar-day basis, per jesse.) |
| **Serenity index** | `(Σreturns − rf) / (ulcer_index × pitfall)`, `pitfall = −CVaR(drawdown_series)/std(returns)`. |
| **Ulcer index** | `√( Σ(drawdown²) / (N−1) )` over the drawdown series. |
| **Recovery factor** | `net_profit / |max_drawdown_abs|`. |
| **Gain-to-pain** | `Σ returns / |Σ negative returns|`. |
| **Kelly criterion** | `win_rate − (1−win_rate)/payoff_ratio`. |

### 4.2 Drawdown / tail

| Metric | Formula |
|--------|---------|
| Max drawdown *(existing)* | `min((cum − cummax)/cummax)`. |
| Max DD duration *(existing)* | longest run of consecutive underwater bars. |
| **Max underwater days** | longest span from a peak until balance ≥ that peak again (jesse `calculate_max_underwater_period`). |
| **VaR 95%** | 5th percentile of the daily returns distribution. |
| **CVaR 95% (expected shortfall)** | mean of returns worse than the 5th percentile. |
| **Tail ratio** | `p95(returns) / |p5(returns)|`. |
| **Common-sense ratio** | `tail_ratio × profit_factor`. |
| **Downside deviation** | annualized std of negative returns only. |
| **Drawdown periods** | scan cum equity: a new all-time-high closes the prior episode; record peak/valley/recovery, depth, and per-episode underwater length. |

### 4.3 Distribution

| Metric | Formula |
|--------|---------|
| **Skew** | `scipy.stats.skew(returns)` (3rd standardized moment). |
| **Kurtosis** | `scipy.stats.kurtosis(returns)` (excess, Fisher). |
| **Avg up month / down month** | mean of monthly returns > 0 / < 0. |

### 4.4 Trade-quality (from round-trips)

| Metric | Formula |
|--------|---------|
| Win rate *(existing, fill-based)* | `wins / total`. C7 re-states it round-trip-based (equal when long-only). |
| Profit factor *(existing)* | `gross_profit / |gross_loss|`. |
| Expectancy *(existing)* | `mean(trip.pnl)` (currency). |
| SQN *(existing)* | `mean(pnl)/std(pnl,ddof=1) × √N`. |
| **Payoff ratio / ratio_avg_win_loss** | `avg_win / |avg_loss|`. |
| **Avg win / avg loss** | mean of positive / negative trip PnL. |
| **Largest win / loss** | `max` / `min` trip PnL. |
| **Longest win/loss streak** | freqtrade `calc_streak`: group consecutive same-sign results, take max run per side. |
| **Current streak** | signed length of the trailing same-sign run. |
| **Avg holding (all / win / loss)** | mean `holding_days` over the respective subset. |
| **Win rate long / short** | `winning_longs / (winning_longs+losing_longs)`; same for short (jesse). |
| **Profit factor long / short** | gross profit / |gross loss| restricted to each direction. |
| **Avg trades per day/week/month** | `total / duration_days`, ×7, ×30.44. |

### 4.5 The 60+ metric catalogue (grouped)

- **Returns (existing 4):** total_return, annual_return, volatility, buy_hold_return.
- **Risk-adjusted (9):** sharpe*, sortino*, calmar*, omega*, cagr, serenity, recovery_factor, gain_to_pain, kelly. `*` = already emitted.
- **Drawdown/tail (9):** max_drawdown*, max_dd_duration*, max_underwater_days, var_95, cvar_95, tail_ratio, common_sense_ratio, downside_deviation, ulcer_index.
- **Distribution (5):** skew, kurtosis, avg_up_month, avg_down_month, monthly_win_rate.
- **Trade quality (16):** total_trades*, win_rate*, profit_factor*, expectancy*, sqn*, avg_win*, avg_loss*, avg_trade_return*, payoff_ratio, largest_win, largest_loss, ratio_avg_win_loss, avg_trade_pnl, breakeven, gross_profit, gross_loss.
- **Streaks (4):** longest_win_streak, longest_loss_streak, max_consecutive_wins*, max_consecutive_losses*, current_streak.
- **Holding (5):** avg_holding_days, avg_winning_holding_days, avg_losing_holding_days, max_holding_days, min_holding_days.
- **Long/Short (8):** long_count, short_count, long_pct, short_pct, win_rate_long, win_rate_short, profit_factor_long, profit_factor_short, long_pnl, short_pnl.
- **Activity/exposure (6):** avg_trades_per_day/week/month, avg_exposure, total_turnover, beta, alpha.
- **Periodic (per bucket):** profit_abs, profit_pct, wins, draws, losses, profit_factor — ×{daily, weekly, monthly, weekday}.

Total distinct scalar metrics ≈ 66, of which **21 already exist** (see §5) and are NOT recomputed in the new sections.

### 4.6 Rolling / tearsheet series (`rolling_stats.py`)

- **returns_series** = `equity.pct_change()` (%).
- **cum_returns** = `(1+returns).cumprod()` (growth of $1).
- **rolling_sharpe** = `(mean/std of returns over window) × √periods`, `window` default 63.
- **rolling_volatility** = `returns.rolling(window).std() × √periods × 100`.
- **rolling_beta** = `cov(returns, benchmark)/var(benchmark)` over the window; benchmark = buy-hold daily returns of the same symbol (already available via `bars_open/close` → derive a benchmark series).
- **exposure_series** = `invested_notional / portfolio_value` per bar (needs broker position value per bar; if unavailable, approximate from fills as a step function 0/1 between entry and exit).
- **turnover_series** = cumulative `Σ|traded notional| / avg equity`.
- **beta / alpha (full-sample)** = OLS of strategy returns on benchmark returns; alpha annualized `× periods`.

---

## 5. Existing vs new metrics (avoid duplication)

**Already returned by `metrics.py` / `_metrics_to_dict` (DO NOT re-emit in new sections):**

`total_return_pct`, `annual_return_pct`, `volatility_pct`, `trading_days`, `sharpe_ratio`, `sortino_ratio`, `calmar_ratio`, `omega_ratio`, `max_drawdown_pct`, `max_drawdown_duration`, `total_trades`, `win_rate_pct`, `profit_factor`, `expectancy`, `avg_win`, `avg_loss`, `avg_trade_return`, `sqn`, `max_consecutive_wins`, `max_consecutive_losses`, `buy_hold_return_pct` — **21 metrics**.

Also already emitted at top level: `equity_curve`, `drawdown_series` (from `compute_drawdown_series`), `monthly_returns` (from `compute_monthly_returns`), `pnl_distribution`. The new `periodic_stats.monthly` is **trade-PnL-based** (per-bucket trade profit), which is semantically different from the existing `monthly_returns` (equity-return heatmap) — both are kept; the monthly heatmap tab is unchanged.

**New in Wave-1 (added by the six new modules):** every metric in §4.5 not marked `*`, all round-trip rows, all periodic buckets, all rolling series, drawdown-periods table, and the entry_tag / exit_reason breakdown tables. `TagMetrics.risk_ratios` is explicitly filtered to exclude the 21 existing metrics.

### 5.1 Reuse map (which existing helper feeds what)

| Existing helper | Reused by |
|-----------------|-----------|
| `compute_drawdown_series` | `drawdown_periods.py` (episode scan) & `rolling_stats` cum-returns base. |
| `compute_monthly_returns` | unchanged; NOT reused by `periodic_stats` (different basis). |
| `_consecutive_stats` | superseded for round-trips by freqtrade-style `calc_streak` in `trade_analytics.py`; keep the existing one for the fill-based `max_consecutive_*` in `metrics`. |
| broker `realized_pnl` on fills | authoritative PnL for every round-trip (reconciliation invariant §4.0). |
| `_series_to_points` downsampling | reused by all `rolling_stats` and `periodic_stats` series serializers. |

---

## 6. Acceptance checklist

- [ ] `POST /backtests/run` response still validates for a strategy with zero tags (defaults applied; single "untagged"/"signal" bucket).
- [ ] `Σ round_trip.pnl == Σ sell_fill.realized_pnl` (reconciliation invariant).
- [ ] All five new sections are optional; Overview/Optimize/MonteCarlo tabs render unchanged with the new payload.
- [ ] `RiskRatios` contains none of the 21 existing metric keys.
- [ ] Each new tab shows an `EmptyState` when its section is null/empty (< 2 trades or < 2 periods).
- [ ] New backend modules each < 400 lines; formulas covered by unit tests (80% target) with golden values cross-checked against jesse/freqtrade fixtures.
```