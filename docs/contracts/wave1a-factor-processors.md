# Wave-1a Interface Contract — Cross-Sectional Processors (B1) & Cost-Aware Fitness (B4)

> **Status:** Contract (design only). No implementation in this document.
> **Scope:** Two Wave-1 features for the QuantBot quant platform.
> **Style baseline:** FastAPI + Pydantic v2 (`backend/app/api/v1/endpoints/quant.py`),
> pandas factor engine (`backend/app/quant/formula_factor.py`,
> `factor_analysis.py`), React Query hooks (`frontend/src/hooks/useFactorAnalysis.ts`).
> **References read for signatures (not copied):**
> `refs/qlib/qlib/data/dataset/processor.py`,
> `refs/AlphaGPT/model_core/backtest.py`.

This contract fixes the public surface (files, function signatures, API request/response
schemas, TS types, behavior) so backend and frontend can be built in parallel against a
frozen boundary. Anything not stated here is an implementation detail left to the builder.

---

## 0. Design Context & Key Decisions

### 0.1 Panel data model (cross-sectional)

The existing engine (`factor_analysis.analyze_factor`, `formula_factor.evaluate_formula`)
operates on a **single symbol**: a `DataFrame` indexed by an ISO time string with OHLCV
columns. Cross-sectional (CS) processing operates **across a universe of symbols at each
timestamp**. Therefore B1 introduces a **panel DataFrame**:

- **Index:** `MultiIndex` with names `("datetime", "instrument")` (mirrors qlib's convention,
  simplified to a 2-level long/tidy layout — NOT qlib's multi-index *columns*).
- **Columns:** flat factor/feature columns plus an optional `label` column.
- A helper adapts the platform's per-symbol bar fetch (`DataService.get_bars`) into this
  panel by fetching each symbol in the universe and concatenating.

Rationale: keeps processors independent of qlib's heavyweight `Serializable`/`InstProcessor`
machinery while preserving the leakage-safe `fit`/`__call__` split that is the whole point.

### 0.2 Leakage-safe split (the core invariant)

Two processor lists, applied in order, exactly as qlib splits them:

| List | Fitted on | Applied to | Purpose |
|------|-----------|-----------|---------|
| `infer_processors` | never fitted (stateless) OR fit is a no-op | **all** rows (train + valid + test) | CS ops that only use same-timestamp data — safe by construction |
| `learn_processors` | **train window only** (`fit_start` → `fit_end`) | **all** rows | time-series/panel ops whose parameters (mean, median, MAD) would leak future info if fit on the full sample |

**Invariant:** any processor that computes a statistic over the time axis MUST be a
`learn_processor` and MUST be `fit()` strictly on `[fit_start, fit_end]`. CS processors
(rank/zscore within one timestamp) are `infer_processors` because they never see other
timestamps and cannot leak. `DropnaLabel` is special: it is `is_for_infer() == False`
(drops rows by label availability) so it must NOT run on the inference/test path — see §4.5.

### 0.3 B4 fitness portability

`MemeBacktest.evaluate` (AlphaGPT) is a torch, meme-coin-flavored routine. B4 ports the
*shape* of that computation to numpy/pandas over the platform's equity/crypto universes and
returns **one scalar** plus an explainability breakdown. No torch dependency.

---

## 1. New Backend Files

All under `backend/app/quant/`. Files kept small and cohesive (<400 lines target).

| Path | Responsibility |
|------|----------------|
| `backend/app/quant/processors.py` | `Processor` base class + concrete processors (`CSRankNorm`, `CSZScoreNorm`, `RobustZScoreNorm`, `Fillna`, `DropnaLabel`). Pure pandas, no I/O. |
| `backend/app/quant/processing_pipeline.py` | `ProcessingPipeline` orchestrator: holds `infer_processors` + `learn_processors`, implements the fit-on-train / apply-on-all flow, plus the processor registry/factory (`build_processor`, `PROCESSOR_META`). |
| `backend/app/quant/panel.py` | Panel adapters: `bars_to_panel(...)`, `attach_forward_label(...)`, `panel_to_records(...)`. Converts platform bars ↔ `(datetime, instrument)` panel. |
| `backend/app/quant/factor_fitness.py` | `compute_factor_fitness(...)` (B4) + `FitnessResult` dataclass + fitness config constants. |

No changes to signatures of existing `formula_factor.py` / `factor_analysis.py`; integration
is additive (see §5).

---

## 2. Backend Internal Signatures (Python)

> These are the module-internal contracts the endpoints depend on. Types are annotated;
> bodies are out of scope. `frozen=True` dataclasses per the project immutability rule.

### 2.1 `processors.py`

```python
class ProcessorError(ValueError):
    """Processor misconfiguration or fit/apply failure."""

class Processor:
    """Base. Stateless by default; stateful processors override fit()."""
    def fit(self, panel: pd.DataFrame) -> "Processor":
        """Learn parameters from the TRAIN-window slice only. Returns a NEW fitted
        instance (immutable: does not mutate self). No-op for stateless processors."""
    def __call__(self, panel: pd.DataFrame) -> pd.DataFrame:
        """Return a NEW transformed panel. MUST NOT mutate the input (differs from
        qlib, which allows in-place). Callers rely on immutability."""
    def is_for_infer(self) -> bool: ...      # default True
    @property
    def is_stateful(self) -> bool: ...       # default False; True => must be a learn_processor
```

Concrete processors (constructor params — all validated, raise `ProcessorError` on bad config):

```python
# infer (stateless, cross-sectional within each datetime)
class CSRankNorm(Processor):
    def __init__(self, fields: list[str] | None = None): ...
    # rank(pct=True) within each datetime, then (r-0.5)*3.46 -> ~unit std, mean 0

class CSZScoreNorm(Processor):
    def __init__(self, fields: list[str] | None = None, method: Literal["zscore","robust"] = "zscore"): ...
    # per-datetime (x-mean)/std ; method="robust" uses median/MAD*1.4826 within the datetime

class Fillna(Processor):
    def __init__(self, fields: list[str] | None = None, fill_value: float = 0.0): ...

# learn (stateful, fit on train window; time/panel statistics)
class RobustZScoreNorm(Processor):        # is_stateful = True
    def __init__(self, fields: list[str] | None = None, clip_outlier: bool = True,
                 clip_bound: float = 3.0): ...
    # fit(): mean_=nanmedian(X), std_=nanmedian(|X-mean_|)*1.4826 + EPS over train slice
    # __call__(): (x-mean_)/std_, optionally clip to [-clip_bound, +clip_bound]

class DropnaLabel(Processor):             # is_for_infer = False
    def __init__(self, label_field: str = "label"): ...
    # drops rows where label is NaN; excluded from the inference/test apply path
```

`fields=None` means "all numeric feature columns except `label`". `clip_bound` replaces
qlib's hardcoded `3` (no magic numbers, per coding-style).

### 2.2 `processing_pipeline.py`

```python
@dataclass(frozen=True)
class ProcessorConfig:
    name: str                      # registry key, e.g. "CSRankNorm"
    params: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class PipelineResult:
    panel: pd.DataFrame            # processed panel (datetime, instrument)-indexed
    fitted_learn: list[str]        # names of learn processors that were fitted
    n_rows_in: int
    n_rows_out: int                # differs when DropnaLabel/Dropna reduced rows
    dropped_rows: int

def build_processor(cfg: ProcessorConfig) -> Processor:
    """Registry factory. Unknown name -> ProcessorError."""

class ProcessingPipeline:
    def __init__(self,
                 infer_processors: list[Processor],
                 learn_processors: list[Processor]):
        """Validation: every learn processor with is_stateful must be here, not in infer;
        every is_for_infer()==False processor must be a learn processor. Raise ProcessorError otherwise."""

    @classmethod
    def from_configs(cls,
                     infer: list[ProcessorConfig],
                     learn: list[ProcessorConfig]) -> "ProcessingPipeline": ...

    def fit(self, panel: pd.DataFrame, fit_start: str, fit_end: str) -> "ProcessingPipeline":
        """Slice panel to [fit_start, fit_end] on the datetime level, fit each learn
        processor on that slice (in list order, threading the partially-processed train
        slice through). Returns a NEW pipeline with fitted processors. Immutable."""

    def process(self, panel: pd.DataFrame, *, for_infer: bool = False) -> PipelineResult:
        """Apply infer_processors then learn_processors, in order, to the FULL panel.
        When for_infer=True, skip processors with is_for_infer()==False (e.g. DropnaLabel)."""

# metadata for the frontend builder
PROCESSOR_META: list[dict]   # [{name, label, kind: "infer"|"learn", params: [{name,type,default,description}]}]
```

### 2.3 `panel.py`

```python
def bars_to_panel(bars_by_symbol: dict[str, list["Bar"]],
                  feature_fn: Callable[[pd.DataFrame], pd.DataFrame] | None = None
                 ) -> pd.DataFrame:
    """Build a (datetime, instrument)-indexed panel from per-symbol bars. If feature_fn
    is given, it maps a single-symbol OHLCV frame -> feature columns before stacking."""

def attach_forward_label(panel: pd.DataFrame, forward_period: int,
                         label_field: str = "label") -> pd.DataFrame:
    """Add a forward-return label per instrument: close.pct_change(p).shift(-p). Immutable."""

def panel_to_records(panel: pd.DataFrame, max_rows: int = 2000) -> list[dict]:
    """Serialize tail of panel to JSON-safe records for API responses (NaN->null)."""
```

### 2.4 `factor_fitness.py` (B4)

```python
@dataclass(frozen=True)
class FitnessConfig:
    fee_rate: float = 0.0010          # one-way fee (10 bps default; AlphaGPT used 60 bps for memes)
    max_impact: float = 0.02          # cap on market-impact slippage per trade
    trade_notional: float = 10_000.0  # assumed order size for impact model
    entry_threshold: float = 0.85     # sigmoid(signal) gate to take a position
    drawdown_bar: float = 0.05        # per-bar loss beyond which a "big drawdown" is counted
    drawdown_penalty: float = 2.0     # score penalty weight per big-drawdown event
    min_activity: int = 5             # minimum active positions or fitness is floored
    inactivity_floor: float = -10.0   # fitness returned when activity gate fails

@dataclass(frozen=True)
class FitnessResult:
    fitness: float                    # THE scalar (median across universe of per-instrument score)
    mean_net_return: float            # cross-universe mean cumulative net PnL (diagnostic)
    gross_return: float               # before costs
    total_cost: float                 # fees + impact slippage, summed
    turnover: float                   # sum of |Δposition|
    avg_activity: float               # mean active bars per instrument
    n_big_drawdowns: int              # total big-drawdown events across universe
    activity_gate_passed: bool
    per_instrument_score: dict[str, float]   # instrument -> score (robustness view)

def compute_factor_fitness(
    factor_panel: pd.DataFrame,       # (datetime, instrument) -> factor value column "factor"
    forward_return_panel: pd.DataFrame,   # aligned target return per (datetime, instrument)
    liquidity_panel: pd.DataFrame | None = None,   # per-cell liquidity (e.g. dollar volume); None => impact=0
    config: FitnessConfig = FitnessConfig(),
) -> FitnessResult:
    """See §4.6 for the exact computation."""
```

---

## 3. API Endpoints

All under the existing quant router (`/api/v1/quant`), POST, Pydantic v2, matching the
`try/except -> HTTPException(422|400|503)` pattern already used in `quant.py`.

### 3.1 `GET /api/v1/quant/processors/meta`

Returns the processor registry for the frontend pipeline builder.

**Response** (`200`): `list[ProcessorMetaModel]`

```
ProcessorMetaModel:
  name: str            — registry key, e.g. "CSRankNorm"
  label: str           — human label, e.g. "截面排名标准化"
  kind: "infer" | "learn"   — which list it belongs to
  is_for_infer: bool   — false for DropnaLabel-style processors
  params: list[ParamMetaModel]

ParamMetaModel:
  name: str            — param name, e.g. "clip_outlier"
  type: "int" | "float" | "bool" | "str" | "list[str]"
  default: object | null
  description: str
```

### 3.2 `POST /api/v1/quant/processors/preview`

Runs a pipeline over a symbol universe and returns before/after factor distributions so the
user can see the leakage-safe transform in action.

**Request** (`ProcessorPreviewRequest`):

```
symbols: list[str]              — universe, min_length 2, max_length 50
market: "US" | "HK" | "A"       — default "US"
frequency: str                  — default "1d"
start: str | null               — ISO date; default end - 2y
end: str | null                 — ISO date; default today
fit_end: str                    — ISO date; train/test boundary (fit_start defaults to `start`)
base_factor: str                — an AVAILABLE_FACTORS name OR "__formula__"
tokens: list[str] | null        — RPN tokens, required when base_factor == "__formula__"; max_length 32
infer_processors: list[ProcessorConfigModel]   — default []
learn_processors: list[ProcessorConfigModel]   — default []
forward_period: int             — default 10, ge 1, le 60 (for label attachment)

ProcessorConfigModel:
  name: str
  params: dict[str, object]     — default {}
```

**Response** (`ProcessorPreviewResponse`):

```
symbols: list[str]
market: str
fit_end: str
n_rows_in: int
n_rows_out: int
dropped_rows: int
fitted_learn: list[str]         — learn processors that were fitted on the train window
columns: list[str]              — factor columns present in the panel
raw_stats: FactorStatsModel     — pre-processing distribution of the base factor
processed_stats: FactorStatsModel  — post-processing distribution
sample_before: list[PanelCellModel]   — tail sample (<=500) pre-processing
sample_after: list[PanelCellModel]    — tail sample (<=500) post-processing

FactorStatsModel:
  count: int
  mean: float
  std: float
  min: float
  p25: float
  median: float
  p75: float
  max: float
  nan_rate: float

PanelCellModel:
  time: str
  instrument: str
  value: float | null
```

Errors: `400` (bad market/frequency/date, formula error, <2 symbols, insufficient data),
`503` (data feed), `422` (processing failure).

### 3.3 `POST /api/v1/quant/factor/fitness` (B4)

Computes the single cost-aware fitness scalar for a candidate factor over a universe.

**Request** (`FactorFitnessRequest`):

```
symbols: list[str]              — universe, min_length 2, max_length 50
market: "US" | "HK" | "A"       — default "US"
frequency: str                  — default "1d"
start: str | null
end: str | null
base_factor: str                — AVAILABLE_FACTORS name OR "__formula__"
tokens: list[str] | null        — required when base_factor == "__formula__"; min_length 1, max_length 32
forward_period: int             — default 5, ge 1, le 60 (target return horizon)
# optional cost-model overrides (all fall back to FitnessConfig defaults):
fee_rate: float | null          — ge 0, le 0.05
max_impact: float | null        — ge 0, le 0.2
trade_notional: float | null    — gt 0
entry_threshold: float | null   — gt 0, lt 1
drawdown_bar: float | null      — gt 0, lt 1
drawdown_penalty: float | null  — ge 0
min_activity: int | null        — ge 0
```

**Response** (`FactorFitnessResponse`):

```
symbols: list[str]
market: str
base_factor: str
tokens: list[str] | null
forward_period: int
fitness: float                  — THE scalar score (higher = better)
mean_net_return: float
gross_return: float
total_cost: float
turnover: float
avg_activity: float
n_big_drawdowns: int
activity_gate_passed: bool
per_instrument_score: Record<instrument, float>   — median input; robustness view
config_used: FitnessConfigModel                    — echoes the effective cost model

FitnessConfigModel:
  fee_rate: float
  max_impact: float
  trade_notional: float
  entry_threshold: float
  drawdown_bar: float
  drawdown_penalty: float
  min_activity: int
```

Errors: `400` (bad params, formula error, insufficient data/universe), `503` (data feed),
`422` (fitness computation failure).

---

## 4. Behavior Notes

### 4.1 `CSRankNorm` (infer)
For each `datetime`, rank the field across instruments with `rank(pct=True)`, then
`(rank - 0.5) * 3.46`. Output ≈ mean 0, unit std, bounded. NaNs keep NaN (not ranked).
Edge case: a timestamp with 1 instrument → rank is 0.5 → output 0. A timestamp where all
values are NaN → all NaN. Leakage-safe: uses only same-timestamp data.

### 4.2 `CSZScoreNorm` (infer)
Per `datetime`: `method="zscore"` → `(x - mean) / (std + EPS)`; `method="robust"` →
`(x - median) / (MAD*1.4826 + EPS)`. Edge cases: std/MAD == 0 (constant cross-section) →
output 0 for that timestamp (guarded by EPS); single instrument → 0. Leakage-safe.

### 4.3 `RobustZScoreNorm` (learn — leakage-sensitive)
`fit()` computes, over the **train window only**, per-column `mean_ = nanmedian(X)` and
`std_ = nanmedian(|X - mean_|) * 1.4826 + EPS`. `__call__` applies `(x - mean_) / std_` to
the **whole** panel and, when `clip_outlier`, clips to `[-clip_bound, clip_bound]`. This is
the classic look-ahead trap: fitting median/MAD on the full sample leaks test-period scale
into training — hence it MUST be a learn processor fitted on `[fit_start, fit_end]`.
Edge cases: column entirely NaN in train → `std_` collapses to `EPS`, output ~0 (documented,
not an error); unseen extreme in test → clipped.

### 4.4 `Fillna` (infer)
Replace NaN with `fill_value` (default 0) on selected fields. Stateless, order matters:
place AFTER normalization so fills don't distort fitted statistics. Note: filling before a
learn-fit would bias the median — the pipeline validator does not enforce ordering, but the
default presets (§6) order it last.

### 4.5 `DropnaLabel` (learn, `is_for_infer=False`)
Drops rows where `label` is NaN (e.g. the last `forward_period` bars per instrument that have
no realized forward return). Because label availability is a training-only concern, this
processor is **skipped when `process(for_infer=True)`** so the inference/test path keeps all
tradable rows. `PipelineResult.dropped_rows` reports how many rows it removed.

### 4.6 B4 fitness computation (ported from `MemeBacktest.evaluate`)
Given the factor panel `F`, forward-return panel `R`, optional liquidity panel `L`, all
aligned on `(datetime, instrument)` and pivoted to `time × instrument` matrices:

1. **Signal → position:** `signal = sigmoid(F)`; `position = (signal > entry_threshold)`.
   A liquidity/safety gate is applied when `L` is present: positions on cells with liquidity
   below an internal floor are zeroed (equity default: no floor unless `L` provided).
2. **Slippage:** `impact = clip(trade_notional / (L + EPS), 0, max_impact)` when `L` present,
   else `impact = 0`. `cost_rate = fee_rate + impact`.
3. **Turnover:** `turnover = |position - position.shift(1 along time)|` (first bar prev=0).
4. **PnL:** `gross = position * R`; `net = gross - turnover * cost_rate`.
5. **Per-instrument score:** `cum = net.sum(over time)`;
   `big_dd = (net < -drawdown_bar).sum(over time)`;
   `score_i = cum_i - big_dd_i * drawdown_penalty`.
6. **Activity gate:** `activity_i = position_i.sum(over time)`; where
   `activity_i < min_activity`, set `score_i = inactivity_floor`.
7. **Robustness:** `fitness = median_i(score_i)` — median across the universe so a couple of
   lucky names cannot dominate. `mean_net_return = mean_i(cum_i)` is reported alongside.

Determinism: no randomness; same inputs → same scalar. Edge cases: empty universe or all-NaN
factor → `activity_gate_passed=False`, `fitness=inactivity_floor`. `R` NaNs treated as 0 PnL
contribution for that cell. Single instrument → median == that instrument's score.

### 4.7 General
- All processors and the pipeline return NEW frames (immutability rule); qlib's in-place
  behavior is explicitly NOT adopted.
- `EPS = 1e-9` shared constant (reuse `formula_factor._EPS` or a module constant; do not
  hardcode inline).
- Universe fetch uses `DataService.get_bars` per symbol; a symbol that fails to fetch or has
  `< 60` bars is dropped from the universe with a note, not a hard failure — unless fewer than
  2 symbols remain, which is a `400`.

---

## 5. Integration Points with Existing Code

- **`formula_factor.evaluate_formula(df, tokens)`** — reused unchanged to produce the base
  factor per symbol when `base_factor == "__formula__"`. Each symbol's single-frame factor is
  stacked into the panel via `panel.bars_to_panel(..., feature_fn=...)`.
- **`factor_analysis._compute_factor(df, name)`** — reused unchanged for built-in
  `base_factor` names. The endpoint maps `AVAILABLE_FACTORS` names through it per symbol.
- **`factor_analysis.AVAILABLE_FACTORS` / `formula_factor.FEATURE_META,OP_META,PRESET_FORMULAS`**
  — the frontend already loads these; the new `base_factor` field accepts any
  `AVAILABLE_FACTORS.name` plus the sentinel `"__formula__"`.
- **Data layer** — same `AsyncSessionLocal` + `DataService` + `MarketEnum`/`FreqEnum` pattern
  as `run_factor_analysis` / `run_formula_factor`; looped over `symbols`.
- **Router** — new endpoints added to the same `APIRouter` in
  `api/v1/endpoints/quant.py`; import the new modules lazily inside handlers (matching the
  existing local-import style in that file) to keep import time low.
- **No breaking changes**: existing `/factor/analyze`, `/factor/formula`, `/factor/list`,
  `/factor/formula/meta` are untouched. B1/B4 are additive.

---

## 6. Default Pipeline Presets (returned by `/processors/meta` consumers)

Recommended leakage-safe default ordering (the frontend should seed the builder with this):

```
infer_processors: [ CSRankNorm() ]                 # or CSZScoreNorm(method="robust")
learn_processors: [ RobustZScoreNorm(clip_outlier=true), DropnaLabel(), Fillna(fill_value=0) ]
```

Order rationale: CS normalize within each day first (safe) → fit robust z-score scale on the
train window → drop unlabeled training rows → fill residual NaNs last so fills never pollute a
fitted statistic.

---

## 7. Frontend TypeScript Interfaces + React Query Hooks

New file: `frontend/src/hooks/useFactorProcessors.ts` (mirrors `useFactorAnalysis.ts` style:
`useQuery` for metadata with `staleTime: Infinity`, `useMutation` for compute endpoints).

```typescript
import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market, Frequency } from "@/types"

// ── Processor metadata ────────────────────────────────────────────
export type ProcessorKind = "infer" | "learn"
export type ProcessorParamType = "int" | "float" | "bool" | "str" | "list[str]"

export interface ProcessorParamMeta {
  name: string
  type: ProcessorParamType
  default: unknown | null
  description: string
}

export interface ProcessorMeta {
  name: string
  label: string
  kind: ProcessorKind
  is_for_infer: boolean
  params: ProcessorParamMeta[]
}

export interface ProcessorConfig {
  name: string
  params: Record<string, unknown>
}

// ── Preview (B1) ──────────────────────────────────────────────────
export interface ProcessorPreviewRequest {
  symbols: string[]
  market: Market
  frequency: Frequency
  start?: string
  end?: string
  fit_end: string
  base_factor: string            // AVAILABLE_FACTORS name or "__formula__"
  tokens?: string[]              // required when base_factor === "__formula__"
  infer_processors: ProcessorConfig[]
  learn_processors: ProcessorConfig[]
  forward_period: number
}

export interface FactorStats {
  count: number
  mean: number
  std: number
  min: number
  p25: number
  median: number
  p75: number
  max: number
  nan_rate: number
}

export interface PanelCell {
  time: string
  instrument: string
  value: number | null
}

export interface ProcessorPreviewResult {
  symbols: string[]
  market: string
  fit_end: string
  n_rows_in: number
  n_rows_out: number
  dropped_rows: number
  fitted_learn: string[]
  columns: string[]
  raw_stats: FactorStats
  processed_stats: FactorStats
  sample_before: PanelCell[]
  sample_after: PanelCell[]
}

// ── Fitness (B4) ──────────────────────────────────────────────────
export interface FitnessConfig {
  fee_rate: number
  max_impact: number
  trade_notional: number
  entry_threshold: number
  drawdown_bar: number
  drawdown_penalty: number
  min_activity: number
}

export interface FactorFitnessRequest {
  symbols: string[]
  market: Market
  frequency: Frequency
  start?: string
  end?: string
  base_factor: string
  tokens?: string[]
  forward_period: number
  fee_rate?: number
  max_impact?: number
  trade_notional?: number
  entry_threshold?: number
  drawdown_bar?: number
  drawdown_penalty?: number
  min_activity?: number
}

export interface FactorFitnessResult {
  symbols: string[]
  market: string
  base_factor: string
  tokens: string[] | null
  forward_period: number
  fitness: number
  mean_net_return: number
  gross_return: number
  total_cost: number
  turnover: number
  avg_activity: number
  n_big_drawdowns: number
  activity_gate_passed: boolean
  per_instrument_score: Record<string, number>
  config_used: FitnessConfig
}

// ── Hooks ─────────────────────────────────────────────────────────
export function useProcessorMeta() {
  return useQuery<ProcessorMeta[]>({
    queryKey: ["processor-meta"],
    queryFn: () => api.get<ProcessorMeta[]>("/api/v1/quant/processors/meta"),
    staleTime: Infinity,
  })
}

export function useProcessorPreview() {
  return useMutation<ProcessorPreviewResult, Error, ProcessorPreviewRequest>({
    mutationFn: (req) =>
      api.post<ProcessorPreviewResult>("/api/v1/quant/processors/preview", req),
  })
}

export function useFactorFitness() {
  return useMutation<FactorFitnessResult, Error, FactorFitnessRequest>({
    mutationFn: (req) =>
      api.post<FactorFitnessResult>("/api/v1/quant/factor/fitness", req),
  })
}
```

---

## 8. Acceptance Checklist (for the implementer)

- [ ] `Processor.__call__` and `.fit` never mutate inputs (return new frames).
- [ ] Every `is_stateful` processor is rejected from `infer_processors` by pipeline validation.
- [ ] `RobustZScoreNorm.fit` uses only `[fit_start, fit_end]` rows; a regression test proves
      identical fitted params whether or not post-`fit_end` data is present.
- [ ] `DropnaLabel` is skipped when `process(for_infer=True)`.
- [ ] B4 returns a deterministic scalar; activity gate floors fitness to `inactivity_floor`
      when `activity < min_activity`.
- [ ] Endpoints follow the `400/422/503` error mapping used elsewhere in `quant.py`.
- [ ] No hardcoded magic numbers (fees, clip bounds, thresholds) — all from config/constants.
- [ ] New modules each < 400 lines; no changes to existing endpoint behavior.
```
