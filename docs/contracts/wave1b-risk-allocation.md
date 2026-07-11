# Wave-1b Interface Contract — Risk Models (D1) & Discrete Allocation (D2)

> Status: CONTRACT (interface only, no implementation)
> Scope: extends the existing portfolio optimizer with better risk/return
> estimators (D1) and adds continuous-weights → integer-shares conversion (D2).
> Reference (read for signatures only, do NOT copy code):
> `refs/PyPortfolioOpt/pypfopt/{risk_models,expected_returns,discrete_allocation}.py`

Anchor files this contract must align with:

- `backend/app/engine/portfolio/optimizer.py` — `optimize_portfolio()`, `OptimizeMethod`, `PortfolioOptResult`
- `backend/app/api/v1/endpoints/portfolio_opt.py` — `POST /api/v1/portfolio/optimize`
- `frontend/src/types/index.ts` — `PortfolioOptRequest`, `PortfolioOptResult`, `PortfolioOptMethod`
- `frontend/src/hooks/usePortfolio.ts` — `usePortfolioOptimize`
- `frontend/src/pages/PortfolioOptimizer.tsx` — optimizer form/result UI

---

## 0. Dependency Decision (D1/D2 blocking prerequisite)

Checked `backend/requirements.txt` and the installed venv
(`backend/.venv/lib/python3.11/site-packages/`):

| Package | In requirements | Installed | Relevance |
|---|---|---|---|
| `scikit-learn` | `scikit-learn==1.8.0` | ✅ yes | Ledoit-Wolf shrinkage (`sklearn.covariance.ledoit_wolf`) |
| `scipy` | `scipy==1.17.1` | ✅ yes | already used by optimizer (SLSQP); LP fallback via `scipy.optimize.milp`/`linprog` |
| `numpy` / `pandas` | ✅ | ✅ | core |
| `cvxpy` | ❌ absent | ❌ **not installed** | PyPortfolioOpt LP allocation solver |
| `ecos` / `cvxopt` | ❌ absent | ❌ not installed | PyPortfolioOpt MIP solvers |
| `PyPortfolioOpt` (`pypfopt`) | ❌ absent | ❌ not installed | reference lib |

### Decision: REIMPLEMENT, do not add PyPortfolioOpt as a dependency.

Rationale (KISS + minimal footprint):

- **D1 shrinkage** needs only `sklearn.covariance.ledoit_wolf` (already present) plus
  pandas `ewm` and numpy `eigh`. Reimplementing `CovarianceShrinkage.ledoit_wolf`
  (constant-variance target), `exp_cov`, `semicovariance`, and `fix_nonpositive_semidefinite`
  is ~150 lines with zero new dependencies. Adopting PyPortfolioOpt would drag in
  `cvxpy` + a MIP solver stack (`ecos`/`osqp`/`scs`) — heavy transitive weight for
  features we mostly do not use.
- **D2 discrete allocation**:
  - `greedy_portfolio` needs **no solver** — pure numpy. Reimplement (primary method).
  - `lp_portfolio` in PyPortfolioOpt requires `cvxpy` + a mixed-integer solver. We can
    instead express the same integer LP with **`scipy.optimize.milp`** (SciPy ≥ 1.9,
    we have 1.17.1) — no new dependency. This is the "lp" method.

Tradeoff to record: reimplementation means we own correctness/tests for the shrinkage
math and the MILP formulation, and we lose PyPortfolioOpt's `single_factor` /
`constant_correlation` shrinkage targets and its `EfficientCVaR`/`HRP` extras. That is
acceptable for Wave-1b — only the constant-variance Ledoit-Wolf target is in scope.
If a future wave needs the richer targets or convex CVaR, revisit adding `cvxpy`.

**Action item:** add `scikit-learn` is already pinned; **no requirements.txt change is
required** for this wave. (Do NOT add `cvxpy`/`pypfopt`.)

---

## 1. New Backend Files

All under `backend/app/engine/portfolio/`. Keep each file focused (<300 lines).

| Path | Responsibility | Key public surface |
|---|---|---|
| `risk_models.py` | Covariance estimators + PSD fixing | `RiskModel` enum, `risk_matrix(prices, method, **kw) -> pd.DataFrame`, `sample_cov`, `exp_cov`, `semicovariance`, `ledoit_wolf_cov`, `fix_nonpositive_semidefinite` |
| `expected_returns.py` | Expected-returns estimators | `ReturnsModel` enum, `expected_returns(prices, method, **kw) -> pd.Series`, `mean_historical_return`, `ema_historical_return`, `capm_return` |
| `discrete_allocation.py` | Continuous weights → integer shares | `AllocationMethod` enum, `DiscreteAllocationResult` dataclass, `allocate(weights, latest_prices, total_value, method) -> DiscreteAllocationResult`, `greedy_allocation(...)`, `lp_allocation(...)` |

Modified backend files:

- `backend/app/engine/portfolio/optimizer.py` — `optimize_portfolio()` gains
  `risk_model` and `expected_returns_method` params; uses the new estimators instead of
  raw `returns.cov()` / `returns.mean()`.
- `backend/app/api/v1/endpoints/portfolio_opt.py` — extend request schema; add
  `POST /allocate` endpoint + schemas.

### 1.1 `risk_models.py` — public signatures

```python
from enum import Enum
import pandas as pd
import numpy as np

TRADING_DAYS = 252  # reuse optimizer constant

class RiskModel(str, Enum):
    SAMPLE_COV      = "sample_cov"        # current behavior (default, back-compat)
    LEDOIT_WOLF     = "ledoit_wolf"       # Ledoit-Wolf shrinkage, constant-variance target
    EXP_COV         = "exp_cov"           # exponentially-weighted covariance
    SEMICOVARIANCE  = "semicovariance"    # downside-only covariance

# Master dispatcher — mirrors PyPortfolioOpt risk_matrix()
def risk_matrix(
    prices: pd.DataFrame,
    method: RiskModel | str = RiskModel.SAMPLE_COV,
    *,
    frequency: int = TRADING_DAYS,
    fix_method: str = "spectral",   # PSD repair strategy, see 4.4
    **kwargs,
) -> pd.DataFrame:
    """Return an ANNUALIZED, PSD covariance matrix (symbols × symbols)."""

def sample_cov(prices, *, frequency=TRADING_DAYS, log_returns=False) -> pd.DataFrame: ...

def exp_cov(prices, *, span=180, frequency=TRADING_DAYS, log_returns=False) -> pd.DataFrame: ...

def semicovariance(prices, *, benchmark=0.000079, frequency=TRADING_DAYS,
                   log_returns=False) -> pd.DataFrame: ...
# benchmark 0.000079 ≈ risk-free daily return; only returns below it count.

def ledoit_wolf_cov(prices, *, frequency=TRADING_DAYS,
                    log_returns=False) -> pd.DataFrame:
    """Constant-variance Ledoit-Wolf shrinkage via sklearn.covariance.ledoit_wolf."""

def fix_nonpositive_semidefinite(matrix: pd.DataFrame,
                                 fix_method: str = "spectral") -> pd.DataFrame:
    """Repair a non-PSD matrix. fix_method ∈ {'spectral','diag'}."""
```

Contract guarantees:

- All estimators return a **DataFrame indexed and columned by symbol**, already
  annualized (`× frequency`), and passed through `fix_nonpositive_semidefinite`.
- Input `prices`: wide DataFrame, index = dates, columns = symbols, NaNs dropped by caller.
- `risk_matrix` raises `ValueError` on unknown method or <2 columns.

### 1.2 `expected_returns.py` — public signatures

```python
from enum import Enum
import pandas as pd

class ReturnsModel(str, Enum):
    MEAN_HISTORICAL = "mean_historical"   # arithmetic/CAGR mean (current behavior, default)
    EMA_HISTORICAL  = "ema_historical"    # exponentially-weighted mean
    CAPM            = "capm"              # CAPM implied returns

def expected_returns(
    prices: pd.DataFrame,
    method: ReturnsModel | str = ReturnsModel.MEAN_HISTORICAL,
    *,
    frequency: int = 252,
    **kwargs,
) -> pd.Series:
    """Return an ANNUALIZED expected-return vector (index = symbols)."""

def mean_historical_return(prices, *, compounding=True, frequency=252,
                           log_returns=False) -> pd.Series: ...

def ema_historical_return(prices, *, compounding=True, span=500, frequency=252,
                          log_returns=False) -> pd.Series: ...

def capm_return(prices, *, market_prices: pd.DataFrame | None = None,
                risk_free_rate=0.05, compounding=True, frequency=252,
                log_returns=False) -> pd.Series:
    """CAPM: rf + beta*(market_excess). If market_prices is None, an
    equal-weighted portfolio of the input assets proxies the market."""
```

Contract guarantees:

- Returns a `pd.Series` indexed by symbol, annualized (fraction, e.g. `0.18` = 18%).
- `risk_free_rate` default aligns with optimizer's `RISK_FREE_RATE = 0.05`.
- Unknown method → `ValueError`.

### 1.3 `discrete_allocation.py` — public signatures

```python
from dataclasses import dataclass, field
from enum import Enum
import pandas as pd

class AllocationMethod(str, Enum):
    GREEDY = "greedy"   # greedy iterative (no solver; default)
    LP     = "lp"       # integer LP via scipy.optimize.milp

@dataclass
class DiscreteAllocationResult:
    method: str
    shares: dict[str, int]            # symbol → integer share count (zero positions dropped)
    leftover_cash: float              # unspent cash
    allocated_value: float            # sum(shares[s] * price[s])
    total_value: float                # input budget
    allocation_weights: dict[str, float]   # realized weight = shares*price / allocated_value
    rmse: float                       # RMSE between realized and target weights
    skipped: list[str] = field(default_factory=list)  # symbols with no price / dropped

def allocate(
    weights: dict[str, float],
    latest_prices: dict[str, float] | pd.Series,
    total_value: float,
    method: AllocationMethod | str = AllocationMethod.GREEDY,
) -> DiscreteAllocationResult: ...

def greedy_allocation(weights, latest_prices, total_value) -> DiscreteAllocationResult: ...
def lp_allocation(weights, latest_prices, total_value) -> DiscreteAllocationResult: ...
```

Contract guarantees / validation (fail fast at boundary):

- `weights` non-empty dict, no NaN; negligible weights (<1e-4) dropped.
- Wave-1b is **long-only**: negative weights → `ValueError` (no short handling in scope,
  unlike PyPortfolioOpt's short sub-portfolio path).
- `latest_prices` must cover every retained symbol and be > 0; missing/≤0 → symbol added
  to `skipped`, weights renormalized over the remainder.
- `total_value > 0` else `ValueError`.
- `leftover_cash >= 0` always; `allocated_value + leftover_cash == total_value` (± fp eps).
- `lp_allocation` objective: minimize `sum|target_value_i - shares_i*price_i| + leftover`
  subject to `shares_i >= 0` integer, `sum(shares_i*price_i) <= total_value`.

---

## 2. API Changes

### 2.1 EXTEND `POST /api/v1/portfolio/optimize`

Add two optional fields to `OptimizePortfolioRequest` (backward compatible — defaults
reproduce today's behavior). Full field list:

```python
class OptimizePortfolioRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=2, max_length=20)
    market: str = Field("US", description="US / HK / A")
    start_date: str = Field(..., description="YYYY-MM-DD")
    end_date: str = Field(..., description="YYYY-MM-DD")
    method: OptimizeMethod = OptimizeMethod.MAX_SHARPE
    include_frontier: bool = True
    # ── NEW (D1) ──
    risk_model: RiskModel = RiskModel.SAMPLE_COV
    expected_returns_method: ReturnsModel = ReturnsModel.MEAN_HISTORICAL
```

Response schema `PortfolioOptResponse` — add echo fields so the UI can display which
estimators were used (optional but recommended):

```python
class PortfolioOptResponse(BaseModel):
    method: str
    weights: dict[str, float]
    expected_return: float
    expected_volatility: float
    sharpe_ratio: float
    cvar_95: float
    frontier: list[dict]
    risk_contributions: dict[str, float]
    # ── NEW echoes ──
    risk_model: str
    expected_returns_method: str
```

`optimize_portfolio()` signature change (optimizer.py):

```python
def optimize_portfolio(
    prices: pd.DataFrame,
    method: OptimizeMethod = OptimizeMethod.MAX_SHARPE,
    include_frontier: bool = True,
    *,
    risk_model: RiskModel = RiskModel.SAMPLE_COV,
    expected_returns_method: ReturnsModel = ReturnsModel.MEAN_HISTORICAL,
) -> PortfolioOptResult:
```

Behavior: replace the inlined
`mu = returns.mean().values * TRADING_DAYS` and
`cov = returns.cov().values * TRADING_DAYS`
with `expected_returns(prices, expected_returns_method)` and
`risk_matrix(prices, risk_model)` respectively (both already annualized — drop the
`* TRADING_DAYS`). `returns_matrix` for CVaR stays as raw daily `pct_change`.
`min_cvar` / `equal_weight` still ignore `mu`; `risk_parity`/`min_volatility` use only
`cov`; `max_sharpe` + frontier use both. `PortfolioOptResult` gains `risk_model` and
`expected_returns_method` string fields echoed into the response.

### 2.2 ADD `POST /api/v1/portfolio/allocate`

New endpoint in `portfolio_opt.py`. Takes already-computed weights + prices + budget
(no market data fetch — pure compute, fast, synchronous-in-threadpool).

Request schema:

```python
class AllocateRequest(BaseModel):
    weights: dict[str, float] = Field(..., description="symbol → continuous weight")
    latest_prices: dict[str, float] = Field(..., description="symbol → latest price")
    total_value: float = Field(..., gt=0, description="cash budget to allocate")
    method: AllocationMethod = AllocationMethod.GREEDY

    @field_validator("weights")
    @classmethod
    def non_empty(cls, v: dict[str, float]) -> dict[str, float]:
        if not v:
            raise ValueError("weights must be non-empty")
        if any(w < 0 for w in v.values()):
            raise ValueError("negative weights not supported (long-only)")
        return v
```

Response schema:

```python
class AllocateResponse(BaseModel):
    method: str
    shares: dict[str, int]
    leftover_cash: float
    allocated_value: float
    total_value: float
    allocation_weights: dict[str, float]
    rmse: float
    skipped: list[str]
```

Endpoint contract:

- `@router.post("/allocate", response_model=AllocateResponse)`
- Runs `allocate(...)` in `asyncio.to_thread`.
- `ValueError` → `HTTPException(400)`; unexpected → `HTTPException(500)`.
- Symbols present in `weights` but missing in `latest_prices` → included in `skipped`,
  not an error, weights renormalized over the rest.

---

## 3. Frontend Changes

### 3.1 `frontend/src/types/index.ts`

Add enums + extend/append interfaces:

```typescript
// ── 组合优化：风险模型 & 预期收益估计 (D1) ──
export type RiskModel =
  | "sample_cov" | "ledoit_wolf" | "exp_cov" | "semicovariance"

export type ExpectedReturnsMethod =
  | "mean_historical" | "ema_historical" | "capm"

// EXTEND existing PortfolioOptRequest (add two optional fields)
export interface PortfolioOptRequest {
  symbols: string[]
  market: Market
  start_date: string
  end_date: string
  method: PortfolioOptMethod
  include_frontier: boolean
  risk_model?: RiskModel                       // NEW, default "sample_cov"
  expected_returns_method?: ExpectedReturnsMethod  // NEW, default "mean_historical"
}

// EXTEND existing PortfolioOptResult (echo which estimators ran)
export interface PortfolioOptResult {
  method: string
  weights: Record<string, number>
  expected_return: number
  expected_volatility: number
  sharpe_ratio: number
  cvar_95: number
  frontier: PortfolioFrontierPoint[]
  risk_contributions: Record<string, number>
  risk_model?: string                 // NEW
  expected_returns_method?: string    // NEW
}

// ── 离散配置 (D2) ──
export type AllocationMethod = "greedy" | "lp"

export interface AllocateRequest {
  weights: Record<string, number>
  latest_prices: Record<string, number>
  total_value: number
  method: AllocationMethod
}

export interface AllocateResult {
  method: string
  shares: Record<string, number>
  leftover_cash: number
  allocated_value: number
  total_value: number
  allocation_weights: Record<string, number>
  rmse: number
  skipped: string[]
}
```

### 3.2 `frontend/src/hooks/usePortfolio.ts`

Keep `usePortfolioOptimize` as-is (its `PortfolioOptRequest` now carries the optional
new fields). Add a second mutation hook:

```typescript
import type {
  PortfolioOptRequest, PortfolioOptResult,
  AllocateRequest, AllocateResult,
} from "@/types"

export function usePortfolioOptimize() {
  return useMutation<PortfolioOptResult, Error, PortfolioOptRequest>({
    mutationFn: (req) => api.post<PortfolioOptResult>("/api/v1/portfolio/optimize", req),
  })
}

// NEW
export function usePortfolioAllocate() {
  return useMutation<AllocateResult, Error, AllocateRequest>({
    mutationFn: (req) => api.post<AllocateResult>("/api/v1/portfolio/allocate", req),
  })
}
```

### 3.3 `frontend/src/pages/PortfolioOptimizer.tsx` (guidance, not required by contract)

- Add two `<select>` controls to the config form bound to `form.risk_model` and
  `form.expected_returns_method`, with option constants mirroring the enums (Chinese
  labels + one-line descriptions, matching existing `METHOD_OPTIONS` style).
- After a successful optimize, offer a "生成整数配股" (discrete allocation) panel: a cash
  input + method toggle (greedy/lp) that calls `usePortfolioAllocate` with
  `result.weights`, the latest prices, and the entered budget; render shares,
  leftover cash, realized-vs-target weights, and RMSE.

---

## 4. Behavior Notes

### 4.1 Risk models — method, when to use

| Model | How it estimates covariance | When to prefer |
|---|---|---|
| `sample_cov` | Plain sample covariance of daily returns × frequency | Baseline; long history, many days ≫ assets |
| `ledoit_wolf` | Shrinks sample cov toward constant-variance target `F` (identity scaled by avg variance); optimal shrinkage intensity δ estimated analytically (`sklearn.covariance.ledoit_wolf`) | **Default recommendation** when assets ≳ days or matrix is ill-conditioned; reduces estimation error and produces more stable, invertible cov → better optimizer weights |
| `exp_cov` | Exponentially-weighted covariance, `span` (default 180 trading days) down-weights old data | Regime-aware; when recent correlations matter more than the distant past |
| `semicovariance` | Covariance of returns **below a benchmark** (default daily rf ≈ 0.000079); zeros out upside co-movement | Downside-risk-focused optimization; pairs naturally with min-volatility intent |

All outputs are annualized and PSD-repaired before returning.

### 4.2 Expected-returns estimators

| Method | How | When |
|---|---|---|
| `mean_historical` | Arithmetic/CAGR mean of returns × frequency | Baseline (current behavior) |
| `ema_historical` | Exponentially-weighted mean, `span` default 500 | Trend-tilted; recent performance weighted higher |
| `capm` | `rf + β·(market_excess_return)`; β vs market proxy (equal-weight of inputs if no `market_prices`) | When you distrust noisy raw historical means and want a risk-model-implied return |

Expected returns are notoriously noisy — `ledoit_wolf` + `capm` or `ema` is a common
"more robust inputs" combination feeding the same SLSQP optimizer.

### 4.3 Discrete allocation algorithms

- **greedy** (default, no solver): buy `floor(weight_i · budget / price_i)` shares in the
  first pass (never overspends), then iteratively spend remaining cash one share at a time
  on whichever asset is most **under-weight** vs target and still affordable, until no
  affordable asset closes its deficit. Fast, always feasible, slightly sub-optimal.
- **lp** (integer LP via `scipy.optimize.milp`): choose integer share counts minimizing
  total dollar deviation from target values plus leftover cash, subject to
  non-negativity and budget. Optimal for the L1 objective but slower; falls back to
  greedy with a warning if the MILP is infeasible/unbounded. (This replaces
  PyPortfolioOpt's `cvxpy` formulation — same objective, SciPy solver, no new dep.)
- Both return realized integer weights + RMSE so the UI can show discretization drift.

### 4.4 PSD fixing

`fix_nonpositive_semidefinite(matrix, fix_method)`:

- `"spectral"` (default): eigendecompose, clip negative eigenvalues to 0, reconstruct.
- `"diag"`: add the smallest amount to the diagonal to push the min eigenvalue ≥ 0.
- Always applied inside `risk_matrix` so downstream `w @ cov @ w` is well-defined and
  SLSQP does not diverge on a slightly-negative-definite sample matrix.

---

## 5. Out of Scope (Wave-1b)

- Short positions in discrete allocation (long-only only).
- PyPortfolioOpt `single_factor` / `constant_correlation` / `oracle_approximating`
  shrinkage targets — only constant-variance Ledoit-Wolf ships now.
- Convex CVaR / HRP optimizers (would require `cvxpy`).
- Persisting allocation results; `/allocate` is stateless compute.

## 6. Test Contract (targets for implementation phase)

- `risk_models`: each estimator returns square symmetric PSD DataFrame; `ledoit_wolf`
  δ ∈ [0,1]; `fix_nonpositive_semidefinite` turns a crafted non-PSD matrix PSD.
- `expected_returns`: shape/index correctness; `capm` with synthetic market recovers β≈1
  for the market proxy itself.
- `discrete_allocation`: `sum(shares*price) + leftover == total_value`; no overspend;
  greedy vs lp both feasible; skipped-symbol renormalization; negative weight → ValueError.
- API: `/optimize` back-compat (omitting new fields reproduces current output);
  `/allocate` happy path + validation 400s.
- Target coverage ≥ 80% on the three new modules.
