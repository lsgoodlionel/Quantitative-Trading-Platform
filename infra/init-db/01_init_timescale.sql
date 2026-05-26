-- TimescaleDB 初始化脚本
-- 参考: refs/qlib/qlib/data/ 的数据存储设计

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 行情 K 线表
CREATE TABLE IF NOT EXISTS bars (
    time        TIMESTAMPTZ     NOT NULL,
    symbol      VARCHAR(30)     NOT NULL,
    market      VARCHAR(5)      NOT NULL,  -- US / HK / A
    frequency   VARCHAR(5)      NOT NULL,  -- 1m / 5m / 15m / 1h / 1d
    open        NUMERIC(14, 4)  NOT NULL,
    high        NUMERIC(14, 4)  NOT NULL,
    low         NUMERIC(14, 4)  NOT NULL,
    close       NUMERIC(14, 4)  NOT NULL,
    volume      BIGINT          NOT NULL DEFAULT 0,
    turnover    NUMERIC(18, 2),
    vwap        NUMERIC(14, 4),
    PRIMARY KEY (time, symbol, market, frequency)
);

SELECT create_hypertable('bars', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_bars_symbol_freq
    ON bars (symbol, frequency, time DESC);

-- Tick 数据表（仅保留近期）
CREATE TABLE IF NOT EXISTS ticks (
    time        TIMESTAMPTZ     NOT NULL,
    symbol      VARCHAR(30)     NOT NULL,
    market      VARCHAR(5)      NOT NULL,
    bid_price   NUMERIC(14, 4),
    ask_price   NUMERIC(14, 4),
    bid_size    INTEGER,
    ask_size    INTEGER,
    last_price  NUMERIC(14, 4),
    last_size   INTEGER,
    PRIMARY KEY (time, symbol, market)
);

SELECT create_hypertable('ticks', 'time', if_not_exists => TRUE);

-- Tick 数据只保留 30 天（自动清理）
SELECT add_retention_policy('ticks', INTERVAL '30 days', if_not_exists => TRUE);

-- 策略绩效时序（实时记录净值曲线）
CREATE TABLE IF NOT EXISTS strategy_equity (
    time        TIMESTAMPTZ     NOT NULL,
    strategy_id UUID            NOT NULL,
    equity      NUMERIC(18, 4)  NOT NULL,
    cash        NUMERIC(18, 4),
    market_val  NUMERIC(18, 4),
    PRIMARY KEY (time, strategy_id)
);

SELECT create_hypertable('strategy_equity', 'time', if_not_exists => TRUE);
