-- 回测历史持久化（V3 · H5）
--
-- 与实验记录器（Redis + MAX_RECORDS=500 滚动淘汰）不同，回测历史落 PostgreSQL/TimescaleDB
-- 且**无容量上限** —— 本特性的目标就是「消除刷新即失」，会悄悄丢数据的介质等于没做。
-- 清理只走 DELETE /api/v1/backtests/history/{id} 手动删除。
--
-- 净值曲线：写入前统一降采样到最多 1000 点（首尾必留）后整条落 JSONB，
-- 完整配置同时保存，因此 rerun 可精确复现。详见
-- backend/app/data/storage/backtest_history.py 的模块 docstring。

CREATE TABLE IF NOT EXISTS backtest_history (
    id                UUID           PRIMARY KEY,
    name              VARCHAR(300)   NOT NULL DEFAULT '',
    strategy_name     VARCHAR(100)   NOT NULL,
    symbol            VARCHAR(30)    NOT NULL,
    market            VARCHAR(10)    NOT NULL,
    frequency         VARCHAR(10)    NOT NULL,
    start_date        DATE           NOT NULL,
    end_date          DATE           NOT NULL,
    initial_cash      NUMERIC(18, 4) NOT NULL,
    final_value       NUMERIC(18, 4) NOT NULL,
    params            JSONB          NOT NULL DEFAULT '{}',
    metrics           JSONB          NOT NULL DEFAULT '{}',
    equity_curve      JSONB          NOT NULL DEFAULT '[]',
    curve_points      INTEGER        NOT NULL DEFAULT 0,
    curve_downsampled BOOLEAN        NOT NULL DEFAULT FALSE,
    note              TEXT           NOT NULL DEFAULT '',
    created_at        TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bt_history_created ON backtest_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bt_history_lookup  ON backtest_history(strategy_name, symbol, created_at DESC);
