# Wave-1D Interface Contract — Dynamic Protections & Multi-Channel Notifications

> **Status:** CONTRACT ONLY — no implementation. This document is the binding
> interface spec for two Wave-1 features. Implementers MUST match the names,
> shapes, endpoints, and behaviors defined here so backend and frontend can be
> built in parallel.
>
> - **E1 — Dynamic Protections / Circuit-Breakers** (`backend/app/oms/protections/`)
> - **E2 — Telegram/Webhook Multi-Channel Notifications** (`backend/app/notify/`)
>
> **References studied** (patterns only, no code copied):
> `refs/freqtrade/.../plugins/protections/*`, `refs/freqtrade/.../rpc/webhook.py`,
> `refs/jesse/.../services/notifier.py`.
>
> **Existing code aligned with:** `app/oms/manager.py`, `app/oms/order.py`,
> `app/risk/engine.py`, `app/risk/models.py`, `app/tasks/notify.py`,
> `app/api/v1/endpoints/{broker_config,risk,alerts}.py`,
> `frontend/src/pages/{Risk,Settings}.tsx`, `frontend/src/types/index.ts`.

---

## Table of Contents

1. [Design Principles & Boundaries](#1-design-principles--boundaries)
2. [E1 — Protections: New Backend Files](#2-e1--protections-new-backend-files)
3. [E1 — Protection Config Schema (Pydantic)](#3-e1--protection-config-schema-pydantic)
4. [E1 — ProtectionResult / Lock Shape](#4-e1--protectionresult--lock-shape)
5. [E1 — Protection Interface & Manager](#5-e1--protection-interface--manager)
6. [E1 — Per-Protection Trigger Logic](#6-e1--per-protection-trigger-logic)
7. [E1 — OMS / Risk Integration (pre-trade consult)](#7-e1--oms--risk-integration-pre-trade-consult)
8. [E1 — Protection API Endpoints](#8-e1--protection-api-endpoints)
9. [E2 — Notify: New Backend Files](#9-e2--notify-new-backend-files)
10. [E2 — Notification Config Schema (Pydantic)](#10-e2--notification-config-schema-pydantic)
11. [E2 — Event → Notification Mapping](#11-e2--event--notification-mapping)
12. [E2 — Webhook Retry / Backoff](#12-e2--webhook-retry--backoff)
13. [E2 — Notify API Endpoints](#13-e2--notify-api-endpoints)
14. [Frontend TypeScript Interfaces](#14-frontend-typescript-interfaces)
15. [Frontend UI Placement](#15-frontend-ui-placement)
16. [Redis Key Map](#16-redis-key-map)
17. [Out of Scope / Future](#17-out-of-scope--future)

---

## 1. Design Principles & Boundaries

- **Protections are advisory, like `RiskEngine`.** They read state and RETURN
  locks; they never mutate orders or force liquidation. The OMS decides to reject
  based on returned locks (mirrors `RiskEngine.pre_trade_check` returning a
  violation list — see `app/risk/engine.py:62`).
- **Immutability.** All result/config objects are `@dataclass(frozen=True)` or
  Pydantic models; managers return new objects, never mutate inputs (matches
  `app/risk/models.py` `RiskRule`/`RiskViolation`).
- **Two lock scopes:** `GLOBAL` (all symbols) and `SYMBOL` (one symbol). This is
  the freqtrade `global_stop` / `stop_per_pair` split, renamed to QuantBot's
  symbol vocabulary (QuantBot uses `symbol` + `market`, not freqtrade `pair`).
- **Notifications are fire-and-forget** through the existing Celery tasks in
  `app/tasks/notify.py`. The new `app/notify/` package adds a config-driven
  dispatcher and channel abstraction; it does NOT replace the Celery tasks, it
  drives them from stored config instead of `settings.*`.
- **Config lives in Redis** exactly like `broker_config:*` (hash + `:version`
  counter for hot reload) — see `app/api/v1/endpoints/broker_config.py:107`.
- **Secrets are masked on read**, never returned in full (reuse the `_mask()`
  convention from `broker_config.py:60`).

---

## 2. E1 — Protections: New Backend Files

All under `backend/app/oms/protections/` (new package). Keep files small
(200–400 lines, high cohesion), one protection per file.

| File | Responsibility |
|------|----------------|
| `backend/app/oms/protections/__init__.py` | Re-export `IProtection`, `ProtectionResult`, `LockScope`, `ProtectionManager`, `build_protection`. |
| `backend/app/oms/protections/base.py` | `LockScope` enum, `ProtectionResult` frozen dataclass, `IProtection` ABC, `TradeRecord` input DTO. |
| `backend/app/oms/protections/config.py` | Pydantic `ProtectionRuleConfig`, `ProtectionType` enum, `ProtectionsConfig`, `default_protections_config()`. |
| `backend/app/oms/protections/stoploss_guard.py` | `StoplossGuard` — halt after N stoplosses in lookback. |
| `backend/app/oms/protections/cooldown_period.py` | `CooldownPeriod` — per-symbol re-entry lock after any trade. |
| `backend/app/oms/protections/max_drawdown.py` | `MaxDrawdownProtection` — global stop when drawdown > threshold. |
| `backend/app/oms/protections/low_profit_pairs.py` | `LowProfitPairs` — lock symbols whose recent aggregate profit is poor. |
| `backend/app/oms/protections/manager.py` | `ProtectionManager` — composes rules, evaluates global + per-symbol, maintains active locks. |
| `backend/app/oms/protections/registry.py` | `build_protection(cfg) -> IProtection` factory mapping `ProtectionType` → class. |
| `backend/app/oms/protections/store.py` | Redis load/save of `ProtectionsConfig` + active-lock persistence (`protections:config`, `protections:locks`). |

New API endpoint file:

| File | Responsibility |
|------|----------------|
| `backend/app/api/v1/endpoints/protections.py` | `GET/PUT /protections/config`, `GET /protections/locks`, `DELETE /protections/locks/{id}`. Registered in `router.py` under prefix `/protections`. |

**Trade-history source.** Protections need closed-trade history. The contract
requires a read-only accessor `get_closed_trades(symbol: str | None, since:
datetime) -> list[TradeRecord]` provided by the OMS (`OrderManager`). Wave-1D
implementation MAY back this by the in-memory `OrderManager._orders` filtered to
terminal statuses; a later wave swaps it for the `orders`/`fills` DB tables. The
protection classes MUST depend only on the `TradeRecord` DTO, never on
`LiveOrder` directly.

---

## 3. E1 — Protection Config Schema (Pydantic)

`backend/app/oms/protections/config.py`

```python
class ProtectionType(str, Enum):
    STOPLOSS_GUARD        = "stoploss_guard"
    COOLDOWN_PERIOD       = "cooldown_period"
    MAX_DRAWDOWN          = "max_drawdown"
    LOW_PROFIT_PAIRS      = "low_profit_pairs"
```

### 3.1 `ProtectionRuleConfig` — one rule in the list

A single flat model carries the union of all params (unused fields ignored per
type). This mirrors the flat `RiskRule` style and keeps the frontend editor
simple. Validation of which fields apply to which type happens in
`build_protection()`.

| Field | Type | Default | Applies to | Meaning |
|-------|------|---------|-----------|---------|
| `type` | `ProtectionType` | — (required) | all | Which protection. |
| `enabled` | `bool` | `True` | all | Toggle without deleting. |
| `stop_duration_minutes` | `int` (`ge=1, le=43200`) | `60` | all | How long the lock lasts once triggered. |
| `lookback_minutes` | `int` (`ge=1, le=43200`) | `1440` | stoploss_guard, max_drawdown, low_profit_pairs | Window of history considered. |
| `trade_limit` | `int` (`ge=1, le=1000`) | `4` | stoploss_guard, max_drawdown | Min number of closed trades before rule can fire. |
| `required_profit` | `float` | `0.0` | stoploss_guard | Only count stoploss exits with profit ratio below this (e.g. `0.0`). |
| `only_per_symbol` | `bool` | `False` | stoploss_guard | If true, disables the global stop; locks only the offending symbol. |
| `max_allowed_drawdown` | `float` (`gt=0, le=1`) | `0.10` | max_drawdown | Drawdown ratio that triggers a global halt (e.g. `0.10` = 10%). |
| `min_profit_ratio` | `float` | `0.0` | low_profit_pairs | Symbol locked when its aggregate profit ratio over lookback is below this. |
| `required_trades` | `int` (`ge=1, le=1000`) | `2` | low_profit_pairs | Min trades on a symbol before judging it "low profit". |

> **Notes for implementers**
> - QuantBot is candle-agnostic at the OMS layer, so — unlike freqtrade — the
>   contract uses **minutes only** (no `*_candles` variants). Do not port the
>   candle branch from `iprotection.py`.
> - `stop_duration_minutes` replaces freqtrade's `stop_duration`; keep the name
>   explicit.
> - `type` is the discriminator; the freqtrade key was `method`.

### 3.2 `ProtectionsConfig` — the whole set

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `is_active` | `bool` | `True` | Master switch; when false, manager returns no locks. |
| `rules` | `list[ProtectionRuleConfig]` | `[]` | Ordered list of protection rules. |

`default_protections_config() -> ProtectionsConfig` MUST return:

```
StoplossGuard      : enabled, lookback=1440, trade_limit=4, required_profit=0.0, stop_duration=60
CooldownPeriod     : enabled, stop_duration=30
MaxDrawdown        : enabled, lookback=1440, trade_limit=5, max_allowed_drawdown=0.10, stop_duration=120
LowProfitPairs     : disabled by default, lookback=1440, required_trades=2, min_profit_ratio=0.0, stop_duration=60
```

`.to_dict()` / round-trip through Redis MUST preserve field order and enum
`.value` strings (JSON-encoded hash value under `protections:config`).

---

## 4. E1 — ProtectionResult / Lock Shape

`backend/app/oms/protections/base.py`

```python
class LockScope(str, Enum):
    GLOBAL = "global"    # blocks entries for all symbols
    SYMBOL = "symbol"    # blocks entries for one symbol
```

### 4.1 `ProtectionResult` (frozen dataclass) — returned by a protection when it fires

| Field | Type | Meaning |
|-------|------|---------|
| `scope` | `LockScope` | Global or symbol-level lock. |
| `until` | `datetime` (tz-aware UTC) | Lock expiry; entries blocked until this instant. |
| `reason` | `str` | Human-readable cause, e.g. `"4 stoplosses in 1440 min, locking for 60 minutes."` |
| `protection_type` | `ProtectionType` | Which rule produced the lock. |
| `symbol` | `str \| None` | Required when `scope == SYMBOL`; `None` for global. |
| `market` | `str \| None` | `US`/`HK`/`A` when symbol-scoped; `None` for global. |
| `side` | `str` | `"*"` by default (both). Reserved for future long/short scoping; keep the field. |

`to_dict()` MUST emit: `scope`, `symbol`, `market`, `reason`, `protection_type`,
`side`, `until` (ISO-8601), plus a derived `active: bool` (`until > now`).

A protection that does NOT fire returns `None` (matches freqtrade
`ProtectionReturn | None`).

### 4.2 `ActiveLock` — a persisted, live lock (what the API/UI consumes)

The manager promotes each `ProtectionResult` into an `ActiveLock` with identity
and timestamps so the UI can list and manually clear them.

| Field | Type | Meaning |
|-------|------|---------|
| `id` | `str` (uuid4) | Stable lock id (for `DELETE /locks/{id}`). |
| `scope` | `LockScope` | Global / symbol. |
| `symbol` | `str \| None` | Symbol if symbol-scoped. |
| `market` | `str \| None` | Market if symbol-scoped. |
| `reason` | `str` | Cause text. |
| `protection_type` | `ProtectionType` | Producing rule. |
| `locked_at` | `datetime` | When first created (UTC). |
| `until` | `datetime` | Expiry (UTC). |
| `active` | `bool` | Derived: `until > now`. |

### 4.3 `TradeRecord` — input DTO to protections

Minimal closed-trade shape the protections read (decoupled from `LiveOrder`):

| Field | Type | Meaning |
|-------|------|---------|
| `symbol` | `str` | Instrument. |
| `market` | `str` | `US`/`HK`/`A`. |
| `side` | `str` | `BUY`/`SELL`. |
| `close_date` | `datetime` | When the position/trade closed (UTC). |
| `profit_ratio` | `float` | Realized P&L as a ratio of cost basis (e.g. `-0.02`). |
| `profit_abs` | `float` | Realized P&L absolute (account currency). |
| `exit_reason` | `str` | e.g. `"stop_loss"`, `"take_profit"`, `"signal"`, `"manual"`. |

`exit_reason == "stop_loss"` (case-insensitive) is the marker StoplossGuard
counts. Wave-1D MAY infer it from a `reject_reason`/tag on the closing order;
the marker string is part of this contract.

---

## 5. E1 — Protection Interface & Manager

### 5.1 `IProtection` ABC — `base.py`

```python
class IProtection(ABC):
    has_global_stop: bool = False
    has_symbol_stop: bool = False

    def __init__(self, cfg: ProtectionRuleConfig) -> None: ...

    @property
    def name(self) -> str: ...            # class name, for logs/UI

    def short_desc(self) -> str: ...      # one-line summary for UI/startup

    @abstractmethod
    def global_stop(
        self, now: datetime, trades: list[TradeRecord], starting_balance: float,
    ) -> ProtectionResult | None: ...

    @abstractmethod
    def stop_per_symbol(
        self, symbol: str, market: str, now: datetime,
        trades: list[TradeRecord], starting_balance: float,
    ) -> ProtectionResult | None: ...
```

- Protections receive the already-filtered trade list (manager passes global vs
  per-symbol slices) plus `now` and `starting_balance`. They do NOT query
  storage themselves (KISS, testable — pure functions of inputs).
- `calculate_lock_until(now) -> now + stop_duration_minutes` is a shared helper
  on the base class.

### 5.2 `ProtectionManager` — `manager.py`

```python
class ProtectionManager:
    def __init__(self, config: ProtectionsConfig, trade_source: TradeSource) -> None: ...

    def update_config(self, config: ProtectionsConfig) -> None: ...   # hot reload

    @property
    def config(self) -> ProtectionsConfig: ...

    # ── pre-trade consult ─────────────────────────────────
    def check_entry(
        self, symbol: str, market: str, now: datetime | None = None,
        starting_balance: float = 0.0,
    ) -> ProtectionResult | None:
        """
        Returns the blocking lock (global first, then symbol) if entry for
        (symbol, market) is currently disallowed, else None.
        Checks live active locks first (cheap), then evaluates rules.
        """

    def evaluate(self, now: datetime | None = None) -> list[ProtectionResult]:
        """Run all enabled global + symbol rules; create/refresh ActiveLocks."""

    def active_locks(self, now: datetime | None = None) -> list[ActiveLock]:
        """Non-expired locks (pruned lazily)."""

    def clear_lock(self, lock_id: str) -> bool:
        """Manually release a lock (UI action). Returns True if removed."""

    def is_globally_locked(self, now: datetime) -> ActiveLock | None: ...
    def is_symbol_locked(self, symbol: str, market: str, now: datetime) -> ActiveLock | None: ...
```

- `TradeSource` is a `Protocol` with
  `get_closed_trades(symbol: str | None, since: datetime) -> list[TradeRecord]`.
  `OrderManager` implements it (or an adapter does).
- **Global singleton** mirroring `get_risk_engine()`:
  `get_protection_manager()` / `init_protection_manager(config, trade_source)`
  in `manager.py`. Initialized in FastAPI lifespan alongside the OMS.
- Manager MUST be immutable-friendly: `update_config` swaps the config
  reference; existing active locks are retained.

---

## 6. E1 — Per-Protection Trigger Logic

### StoplossGuard (`has_global_stop=True`, `has_symbol_stop=True`)
- Look back `lookback_minutes`. Count closed trades where
  `exit_reason == "stop_loss"` **and** `profit_ratio < required_profit`.
- If `count >= trade_limit` → fire.
  - `global_stop`: returns `GLOBAL` lock unless `only_per_symbol` is true (then
    global returns `None`).
  - `stop_per_symbol`: counts only that symbol's stoploss trades; returns
    `SYMBOL` lock.
- `until = calculate_lock_until(now)`; `reason` includes count, window, duration.

### CooldownPeriod (`has_global_stop=False`, `has_symbol_stop=True`)
- `global_stop` always returns `None`.
- `stop_per_symbol`: if ANY closed trade for the symbol exists within
  `stop_duration_minutes` before `now`, lock the symbol until
  `last_close + stop_duration_minutes`. Prevents immediate re-entry.
- `lookback` is not used; the cooldown window IS `stop_duration_minutes`.

### MaxDrawdownProtection (`has_global_stop=True`, `has_symbol_stop=False`)
- `stop_per_symbol` returns `None`.
- `global_stop`: over `lookback_minutes`, require at least `trade_limit` closed
  trades. Compute peak-to-trough drawdown of the cumulative `profit_abs` equity
  curve within the window (relative to `starting_balance` + pre-window cumulative
  profit). If `drawdown > max_allowed_drawdown` → `GLOBAL` lock for
  `stop_duration_minutes`.
- Drawdown is a positive ratio in `[0,1]`. Reason cites actual vs allowed.

### LowProfitPairs (`has_global_stop=False`, `has_symbol_stop=True`)
- `global_stop` returns `None`.
- `stop_per_symbol`: over `lookback_minutes`, if the symbol has
  `>= required_trades` closed trades and their **aggregate** `profit_ratio` (sum
  or mean — implementer picks mean, documents it) is `< min_profit_ratio`, lock
  the symbol for `stop_duration_minutes`. Locks chronically unprofitable symbols.

All four: return `None` when disabled, when `is_active` is false, or when the
trigger condition is not met.

---

## 7. E1 — OMS / Risk Integration (pre-trade consult)

The OMS consults protections **inside `submit_order`, after the existing
`_pre_trade_risk_check` and before routing to the gateway** — i.e. between
`app/oms/manager.py:116` and `:118`.

Contract for the new step in `OrderManager.submit_order`:

```
# after self._pre_trade_risk_check(order)
lock = self._protections.check_entry(order.symbol, order.market, now=utcnow())
if lock is not None:
    order.status = LiveOrderStatus.REJECTED
    order.reject_reason = f"[PROTECTION:{lock.protection_type.value}] {lock.reason}"
    # publish + emit notification (see §11), then return the rejected order
    await self._publish_order_event(order)
    self._notify_protection(lock)          # dispatch to notify layer
    return order
```

Rules:
- **Only entries are gated.** A protection lock blocks position-opening orders.
  The OMS distinguishes entries from exits; if `strategy_id` semantics don't yet
  encode this in Wave-1D, gate `BUY` orders only and document the limitation.
  Exits/closes MUST never be blocked (never trap a position).
- `OrderManager` gains an optional `protection_manager` dependency (constructor
  param, default `None`). When `None`, protections are skipped (backward
  compatible with existing paper/hybrid init functions).
- The manager's `evaluate()` is also driven **on fill** — after a closing trade
  is recorded, call `protection_manager.evaluate()` so freshly-tripped locks
  exist before the next entry. Analogous to `RiskEngine.on_fill`
  (`app/risk/engine.py:149`). A background sweep (reuse the OMS `_poll_loop`
  cadence, `_POLL_INTERVAL=5s`) MAY also call `evaluate()`.
- **`RiskEngine` stays independent.** Protections are a sibling gate, not a rule
  inside `RiskConfig`. Rationale: protections are time/history-driven with
  scoped locks; `RiskRule` is a flat threshold check. Keeping them separate
  avoids overloading `RuleType`.

---

## 8. E1 — Protection API Endpoints

`backend/app/api/v1/endpoints/protections.py`, registered under `/protections`.

| Method & Path | Body | Response | Purpose |
|---------------|------|----------|---------|
| `GET /api/v1/protections/config` | — | `ProtectionsConfig` | Current config from Redis (or defaults). |
| `PUT /api/v1/protections/config` | `ProtectionsConfig` | `ProtectionsConfig` | Persist to Redis, `incr protections:config:version`, hot-reload manager. |
| `GET /api/v1/protections/locks` | — | `{ "locks": ActiveLock[], "count": number }` | List active locks (for Risk page). |
| `DELETE /api/v1/protections/locks/{lock_id}` | — | `{ "cleared": lock_id }` (404 if unknown) | Manually release a lock. |

- Storage & versioning follow `broker_config.py` exactly (Redis hash + `:version`
  incr triggers manager rebuild). Key: `protections:config` (single JSON field
  `data`, or field-per-key — implementer choice, documented).
- `PUT` validates via Pydantic; unknown `ProtectionType` → `422`.
- These endpoints do not require secret masking (no credentials in protection
  config).

---

## 9. E2 — Notify: New Backend Files

All under `backend/app/notify/` (new package). The existing Celery tasks in
`app/tasks/notify.py` are RETAINED and become the transport the dispatcher calls;
they are refactored to read channel config (see §10) instead of `settings.*`.

| File | Responsibility |
|------|----------------|
| `backend/app/notify/__init__.py` | Re-export `NotifyConfig`, `ChannelConfig`, `NotifyEvent`, `dispatch_event`, `get_notify_config`. |
| `backend/app/notify/config.py` | Pydantic `ChannelType`, `NotifyEventType`, `TelegramChannelConfig`, `WebhookChannelConfig`, `ChannelConfig`, `NotifyConfig`, `default_notify_config()`. |
| `backend/app/notify/store.py` | Redis load/save (`notify:config`, `notify:config:version`) + secret masking on read. |
| `backend/app/notify/events.py` | `NotifyEvent` DTO + `render_event(event, channel) -> RenderedMessage` (text for Telegram, dict for webhook). |
| `backend/app/notify/dispatcher.py` | `dispatch_event(event)` — loads config, filters channels by `enabled` + event subscription, enqueues Celery sends. |
| `backend/app/notify/channels/telegram.py` | Telegram send helper (thin; Celery task calls it). |
| `backend/app/notify/channels/webhook.py` | Webhook send helper with retry/backoff (see §12). |

New API endpoint file:

| File | Responsibility |
|------|----------------|
| `backend/app/api/v1/endpoints/notify.py` | `GET/PUT /notify/config`, `POST /notify/test`. Registered in `router.py` under prefix `/notify`. |

Refactor (not new): `app/tasks/notify.py`
- `send_telegram(message, *, token, chat_id)` and
  `send_webhook(payload, *, url, format, timeout, retries, retry_delay)` gain
  explicit params so the dispatcher passes stored config. Backward-compatible
  fallback to `settings.*` is allowed but deprecated. `send_risk_alert` stays as
  a convenience wrapper that builds a `NotifyEvent` and calls `dispatch_event`.

---

## 10. E2 — Notification Config Schema (Pydantic)

`backend/app/notify/config.py`

```python
class ChannelType(str, Enum):
    TELEGRAM = "telegram"
    WEBHOOK  = "webhook"

class NotifyEventType(str, Enum):
    TRADE_FILL    = "trade_fill"       # order filled / partially filled
    ORDER_REJECT  = "order_reject"     # rejected (incl. protection/risk block)
    PNL_UPDATE    = "pnl_update"       # realized P&L threshold crossed
    POSITION      = "position"         # position opened/closed
    DAILY_SUMMARY = "daily_summary"    # end-of-day rollup
    RISK_ALERT    = "risk_alert"       # RiskEngine violation
    PROTECTION    = "protection"       # a protection lock fired

class WebhookFormat(str, Enum):
    JSON = "json"
    FORM = "form"
    RAW  = "raw"
```

### 10.1 `TelegramChannelConfig`

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `bot_token` | `str` (`min_length=1`) | — | Telegram bot token (write-only; masked on read). |
| `chat_id` | `str` (`min_length=1`) | — | Target chat id. |
| `parse_mode` | `Literal["HTML","Markdown"]` | `"HTML"` | Telegram formatting. |

### 10.2 `WebhookChannelConfig`

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `url` | `str` (`min_length=1`, http/https) | — | Endpoint POSTed to. |
| `format` | `WebhookFormat` | `JSON` | `json` body, `form` urlencoded, or `raw` text (`payload["data"]`). |
| `timeout_seconds` | `int` (`ge=1, le=60`) | `10` | Per-request timeout. |
| `retries` | `int` (`ge=0, le=10`) | `2` | Additional attempts after the first (see §12). |
| `retry_delay_seconds` | `float` (`ge=0, le=30`) | `1.0` | Base backoff delay. |
| `secret_header` | `str \| None` | `None` | Optional header name for a shared-secret/HMAC value. |
| `secret_value` | `str \| None` | `None` | Value for `secret_header` (masked on read). |

### 10.3 `ChannelConfig` — one configured channel

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `id` | `str` | uuid4 | Stable channel id. |
| `type` | `ChannelType` | — | telegram / webhook. |
| `name` | `str` (`max_length=60`) | `""` | User label. |
| `enabled` | `bool` | `True` | Toggle. |
| `events` | `list[NotifyEventType]` | `[]` | Which events this channel receives (empty = none). |
| `telegram` | `TelegramChannelConfig \| None` | `None` | Present when `type=telegram`. |
| `webhook` | `WebhookChannelConfig \| None` | `None` | Present when `type=webhook`. |

Validator: exactly one of `telegram`/`webhook` set, matching `type` → else `422`.

### 10.4 `NotifyConfig` — whole config (stored in Redis)

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `is_active` | `bool` | `True` | Master switch. |
| `channels` | `list[ChannelConfig]` | `[]` | All configured channels. |
| `min_pnl_notify_abs` | `float` (`ge=0`) | `0.0` | Suppress `pnl_update` below this absolute P&L move. |
| `daily_summary_time` | `str` (`HH:MM`) | `"16:30"` | Local time to emit `daily_summary`. |

### 10.5 Read masking (response models)

`GET` returns `*_status` variants that never expose secrets:

| Model | Fields |
|-------|--------|
| `TelegramChannelStatus` | `configured: bool`, `token_hint: str \| None` (masked, e.g. `12••••cd`), `chat_id: str`, `parse_mode`. |
| `WebhookChannelStatus` | `url`, `format`, `timeout_seconds`, `retries`, `retry_delay_seconds`, `has_secret: bool`. |
| `ChannelStatus` | `id`, `type`, `name`, `enabled`, `events`, `telegram: TelegramChannelStatus \| None`, `webhook: WebhookChannelStatus \| None`. |
| `NotifyConfigStatus` | `is_active`, `channels: ChannelStatus[]`, `min_pnl_notify_abs`, `daily_summary_time`. |

`PUT` accepts the full `NotifyConfig`. To support "keep existing secret", a blank
`bot_token`/`secret_value` on PUT means "unchanged" (merge with stored) — mirror
the Alpaca form's behavior where a blank secret keeps the old one.

`default_notify_config()` returns `is_active=True, channels=[]` (nothing sends
until the user configures a channel).

---

## 11. E2 — Event → Notification Mapping

Producers build a `NotifyEvent` and call `dispatch_event(event)`. The dispatcher
loads `NotifyConfig`, and for each `enabled` channel whose `events` contains
`event.type`, renders and enqueues a Celery send.

### 11.1 `NotifyEvent` DTO (`events.py`)

| Field | Type | Meaning |
|-------|------|---------|
| `type` | `NotifyEventType` | Event kind. |
| `title` | `str` | Short headline. |
| `symbol` | `str \| None` | Instrument if applicable. |
| `market` | `str \| None` | Market if applicable. |
| `payload` | `dict[str, Any]` | Structured fields (order id, qty, price, pnl, severity, until, …). |
| `created_at` | `datetime` | UTC. |

### 11.2 Producer → Event table

| Source (existing code) | Trigger | `NotifyEventType` | Key payload fields |
|------------------------|---------|-------------------|--------------------|
| `OrderManager._apply_broker_update` when status→`FILLED`/`PARTIAL` (`manager.py:273`) | fill detected | `TRADE_FILL` | `order_id, symbol, market, side, filled_qty, avg_fill_price` |
| `OrderManager.submit_order` reject path (§7) | protection/risk/gateway reject | `ORDER_REJECT` | `order_id, symbol, reason` |
| `RiskEngine.on_fill` / P&L cross | realized P&L past `min_pnl_notify_abs` | `PNL_UPDATE` | `symbol, realized_pnl, portfolio_value` |
| OMS position open/close | position lifecycle | `POSITION` | `symbol, market, qty, avg_cost, event: "opened"\|"closed"` |
| Scheduled task at `daily_summary_time` | daily rollup (reuse `RiskEngine.daily_summary`, `engine.py:231`) | `DAILY_SUMMARY` | `date, orders_today, realized_pnl_today, peak_portfolio_value` |
| `RiskEngine` violation (existing `send_risk_alert`) | BLOCK/HALT violation | `RISK_ALERT` | `severity, message, portfolio_value` |
| `ProtectionManager` lock created / `_notify_protection` (§7) | lock fires | `PROTECTION` | `protection_type, scope, symbol, reason, until` |

### 11.3 Rendering

`render_event(event, channel)`:
- **Telegram** → formatted text string (HTML or Markdown per channel), reusing
  the emoji/label style already in `send_risk_alert` (`tasks/notify.py:67`).
  Escape `_` for Markdown as jesse's `_format_msg` does; cap length at 2000 chars.
- **Webhook** → a dict: `{ "event": type.value, "title", "symbol", "market",
  "timestamp", **payload }`. For `format=raw`, wrap rendered text as
  `{"data": "<text>"}`.

Dispatch is best-effort: a failing channel MUST NOT block others or the caller
(errors logged, Celery handles retry). Producers call `dispatch_event`
fire-and-forget (Celery `.delay`), never awaited in the order hot path.

---

## 12. E2 — Webhook Retry / Backoff

Ported from `freqtrade/rpc/webhook.py:_send_msg` semantics, with exponential
backoff added:

- Attempt the POST up to `1 + retries` times total.
- On a `RequestException` / non-2xx (`raise_for_status`), wait then retry.
- **Backoff:** delay before attempt *n* (1-indexed retries) =
  `retry_delay_seconds * (2 ** (n - 1))`, capped at 30s. (freqtrade used a flat
  delay; this contract mandates exponential.)
- Respect `timeout_seconds` per attempt.
- Format handling matches freqtrade: `json` → `json=payload`; `form` →
  `data=payload`; `raw` → `data=payload["data"]` with
  `Content-Type: text/plain`.
- After exhausting attempts, log a warning and give up (no exception bubbles to
  the producer). At the Celery-task layer, `send_webhook` keeps
  `max_retries`/`default_retry_delay` as an outer safety net, but the in-helper
  loop is the primary mechanism so a single logical send does its own backoff.
- Telegram sends: single attempt with Celery-level retry
  (`max_retries=3, default_retry_delay=30`, as already configured at
  `tasks/notify.py:21`).

---

## 13. E2 — Notify API Endpoints

`backend/app/api/v1/endpoints/notify.py`, registered under `/notify`.

| Method & Path | Body | Response | Purpose |
|---------------|------|----------|---------|
| `GET /api/v1/notify/config` | — | `NotifyConfigStatus` | Masked config for the Settings UI. |
| `PUT /api/v1/notify/config` | `NotifyConfig` | `NotifyConfigStatus` | Persist to Redis, `incr notify:config:version`, hot-reload. Blank secrets = keep existing. |
| `POST /api/v1/notify/test` | `NotifyTestRequest` | `NotifyTestResponse` | Send a synthetic test event to one channel and report success/error. |

`NotifyTestRequest`:

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `channel_id` | `str` | — | Which stored channel to test. |
| `event_type` | `NotifyEventType` | `TRADE_FILL` | Event kind to simulate. |

`NotifyTestResponse` (envelope mirrors `TestConnectionResponse` in
`broker_config.py:51`):

| Field | Type | Meaning |
|-------|------|---------|
| `ok` | `bool` | Whether the send succeeded. |
| `channel_id` | `str` | Echo. |
| `detail` | `str \| None` | Provider message / masked target on success. |
| `error` | `str \| None` | Error text on failure (provider message extracted from JSON as in `broker_config.py:174`). |

- `POST /notify/test` sends **synchronously** (not via Celery) so the UI gets an
  immediate pass/fail, exactly like `test_alpaca_connection`.
- Storage/versioning identical to `broker_config` / protections.

---

## 14. Frontend TypeScript Interfaces

Add to `frontend/src/types/index.ts`. New hooks: `frontend/src/hooks/useProtections.ts`,
`frontend/src/hooks/useNotifyConfig.ts` (follow the `useRisk.ts` /
`useBrokerConfig.ts` react-query pattern).

### 14.1 Protections

```typescript
export type ProtectionType =
  | "stoploss_guard"
  | "cooldown_period"
  | "max_drawdown"
  | "low_profit_pairs"

export type LockScope = "global" | "symbol"

export interface ProtectionRuleConfig {
  type: ProtectionType
  enabled: boolean
  stop_duration_minutes: number
  lookback_minutes?: number
  trade_limit?: number
  required_profit?: number
  only_per_symbol?: boolean
  max_allowed_drawdown?: number
  min_profit_ratio?: number
  required_trades?: number
}

export interface ProtectionsConfig {
  is_active: boolean
  rules: ProtectionRuleConfig[]
}

export interface ActiveLock {
  id: string
  scope: LockScope
  symbol: string | null
  market: Market | null
  reason: string
  protection_type: ProtectionType
  locked_at: string   // ISO-8601
  until: string       // ISO-8601
  active: boolean
}
```

### 14.2 Notifications

```typescript
export type ChannelType = "telegram" | "webhook"
export type WebhookFormat = "json" | "form" | "raw"

export type NotifyEventType =
  | "trade_fill"
  | "order_reject"
  | "pnl_update"
  | "position"
  | "daily_summary"
  | "risk_alert"
  | "protection"

// Request models (what PUT sends)
export interface TelegramChannelConfig {
  bot_token: string          // blank on edit = keep existing
  chat_id: string
  parse_mode: "HTML" | "Markdown"
}

export interface WebhookChannelConfig {
  url: string
  format: WebhookFormat
  timeout_seconds: number
  retries: number
  retry_delay_seconds: number
  secret_header?: string | null
  secret_value?: string | null   // blank on edit = keep existing
}

export interface ChannelConfig {
  id: string
  type: ChannelType
  name: string
  enabled: boolean
  events: NotifyEventType[]
  telegram?: TelegramChannelConfig | null
  webhook?: WebhookChannelConfig | null
}

export interface NotifyConfig {
  is_active: boolean
  channels: ChannelConfig[]
  min_pnl_notify_abs: number
  daily_summary_time: string     // "HH:MM"
}

// Response models (masked — what GET returns)
export interface TelegramChannelStatus {
  configured: boolean
  token_hint: string | null
  chat_id: string
  parse_mode: "HTML" | "Markdown"
}

export interface WebhookChannelStatus {
  url: string
  format: WebhookFormat
  timeout_seconds: number
  retries: number
  retry_delay_seconds: number
  has_secret: boolean
}

export interface ChannelStatus {
  id: string
  type: ChannelType
  name: string
  enabled: boolean
  events: NotifyEventType[]
  telegram?: TelegramChannelStatus | null
  webhook?: WebhookChannelStatus | null
}

export interface NotifyConfigStatus {
  is_active: boolean
  channels: ChannelStatus[]
  min_pnl_notify_abs: number
  daily_summary_time: string
}

export interface NotifyTestResponse {
  ok: boolean
  channel_id: string
  detail: string | null
  error: string | null
}
```

### 14.3 Hook signatures (react-query)

```typescript
// useProtections.ts
useProtectionsConfig(): UseQueryResult<ProtectionsConfig>              // GET  /api/v1/protections/config
useUpdateProtectionsConfig(): UseMutationResult<ProtectionsConfig, Error, ProtectionsConfig>  // PUT
useActiveLocks(): UseQueryResult<{ locks: ActiveLock[]; count: number }> // GET /api/v1/protections/locks (refetchInterval 15s)
useClearLock(): UseMutationResult<{ cleared: string }, Error, string>    // DELETE /locks/{id}

// useNotifyConfig.ts
useNotifyConfig(): UseQueryResult<NotifyConfigStatus>                  // GET  /api/v1/notify/config
useUpdateNotifyConfig(): UseMutationResult<NotifyConfigStatus, Error, NotifyConfig>  // PUT
useTestChannel(): UseMutationResult<NotifyTestResponse, Error, { channel_id: string; event_type?: NotifyEventType }>  // POST /notify/test
```

---

## 15. Frontend UI Placement

### 15.1 Protections → **Risk page** (`frontend/src/pages/Risk.tsx`)
- Add a new card **"动态保护 / 熔断"** in the left column, below the existing
  "风控规则" card. Reuse the `RuleRow` toggle/severity visual language.
- Each `ProtectionRuleConfig` renders as a labeled row with an enable toggle and
  its type-specific numeric params (only show the fields that apply to that
  `type`). A master `is_active` switch at the card header.
- Add an **"当前锁定"** panel in the right sidebar (near "今日汇总") listing
  `ActiveLock[]` from `useActiveLocks()`, each with scope/symbol/reason/until and
  a "解除" (clear) button calling `useClearLock`. Reuse the red-alert card styling
  already used for violations.
- Labels dictionary parallel to `RULE_LABELS` (e.g. `stoploss_guard` →
  "止损熔断").

### 15.2 Notifications → **Settings page** (`frontend/src/pages/Settings.tsx`)
- Add a new `<Section title="通知渠道 — Telegram / Webhook">` after the Alpaca
  section, following the exact `AlpacaConfigSection` interaction pattern
  (status row, masked hints, edit form, test button).
- A `NotifyChannelsSection` component: list configured `ChannelStatus`, an
  "添加渠道" button opening a type-select (telegram/webhook) + fields, per-channel
  event checkboxes (`NotifyEventType[]`), enable toggle, and a "测试" button per
  channel calling `useTestChannel` (shows the same ✓/✗ inline result style as
  `handleTest` in Settings, `Settings.tsx:283`).
- Blank secret fields on edit = keep existing (same UX as Alpaca secret input).

---

## 16. Redis Key Map

| Key | Type | Producer | Purpose |
|-----|------|----------|---------|
| `protections:config` | hash/JSON | `PUT /protections/config` | Serialized `ProtectionsConfig`. |
| `protections:config:version` | int | same | `incr` triggers manager hot reload. |
| `protections:locks` | hash | `ProtectionManager` | Persisted `ActiveLock`s (survive restart). |
| `notify:config` | hash/JSON | `PUT /notify/config` | Serialized `NotifyConfig` (secrets stored plaintext, never returned). |
| `notify:config:version` | int | same | `incr` triggers dispatcher config reload. |

All follow the `broker_config:*` precedent (`broker_config.py:107–114`).

---

## 17. Out of Scope / Future

- **Remote two-way control commands** (Telegram bot receiving `/status`,
  `/stop`, `/close` commands to control the bot) — **explicitly out of scope**
  for this contract. The `NotifyEventType`/channel design is one-way (outbound)
  only. A future wave adds an inbound command router; the `ChannelConfig` may
  later grow a `allow_commands: bool` flag, but it is NOT part of this contract.
- **Discord / Slack / email channels** — the `ChannelType` enum is extensible;
  only `telegram` and `webhook` are in scope now.
- **DB-backed trade history** — Wave-1D reads from the in-memory `OrderManager`;
  swapping `TradeSource` to Postgres `orders`/`fills` tables is a later wave and
  requires no contract change (the `TradeRecord` DTO is the stable boundary).
- **Long/short lock scoping** — the `side` field on `ProtectionResult` is
  reserved (`"*"`) but not exercised until short selling lands.
- **Per-strategy protections** — current scope is global + per-symbol; strategy
  scoping is future.
