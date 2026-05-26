-- 业务数据库初始化

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    email       VARCHAR(255)    NOT NULL UNIQUE,
    hashed_pw   VARCHAR(255)    NOT NULL,
    role        VARCHAR(20)     NOT NULL DEFAULT 'trader',  -- admin/trader/viewer
    is_active   BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- API Keys（用于外部接入）
CREATE TABLE IF NOT EXISTS api_keys (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID            NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        VARCHAR(100)    NOT NULL,
    key_hash    VARCHAR(255)    NOT NULL UNIQUE,
    scopes      TEXT[]          NOT NULL DEFAULT '{}',
    expires_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 券商账户配置
CREATE TABLE IF NOT EXISTS broker_accounts (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID            NOT NULL REFERENCES users(id),
    name        VARCHAR(100)    NOT NULL,
    gateway     VARCHAR(30)     NOT NULL,   -- alpaca / futu / ibkr / ctp_stub
    market      VARCHAR(5)      NOT NULL,   -- US / HK / A / FUTURES
    mode        VARCHAR(10)     NOT NULL DEFAULT 'paper',  -- paper / live
    config      JSONB           NOT NULL DEFAULT '{}',     -- 加密存储的连接配置
    is_active   BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 策略定义
CREATE TABLE IF NOT EXISTS strategies (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID            NOT NULL REFERENCES users(id),
    name        VARCHAR(100)    NOT NULL,
    description TEXT,
    preset      VARCHAR(50),                -- 预设策略名 (可选)
    code        TEXT,                       -- 自定义策略代码 (可选)
    config      JSONB           NOT NULL DEFAULT '{}',  -- 策略参数
    markets     VARCHAR(5)[]    NOT NULL DEFAULT '{}',  -- 运行市场
    status      VARCHAR(20)     NOT NULL DEFAULT 'draft',
    -- draft / backtesting / paper / live / stopped / error
    account_id  UUID            REFERENCES broker_accounts(id),
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_strategies_user ON strategies(user_id, status);

-- 订单表
CREATE TABLE IF NOT EXISTS orders (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id     VARCHAR(100),           -- 券商侧订单ID
    strategy_id     UUID            REFERENCES strategies(id),
    account_id      UUID            REFERENCES broker_accounts(id),
    symbol          VARCHAR(30)     NOT NULL,
    market          VARCHAR(5)      NOT NULL,
    direction       VARCHAR(5)      NOT NULL,   -- BUY / SELL
    order_type      VARCHAR(20)     NOT NULL,   -- MARKET / LIMIT / STOP / STOP_LIMIT
    qty             INTEGER         NOT NULL,
    price           NUMERIC(14, 4),
    stop_price      NUMERIC(14, 4),
    status          VARCHAR(20)     NOT NULL DEFAULT 'pending',
    -- pending / submitted / partial / filled / cancelled / rejected
    filled_qty      INTEGER         NOT NULL DEFAULT 0,
    avg_fill_price  NUMERIC(14, 4),
    commission      NUMERIC(10, 4)  DEFAULT 0,
    reject_reason   TEXT,
    submitted_at    TIMESTAMPTZ,
    filled_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at DESC);

-- 持仓快照表
CREATE TABLE IF NOT EXISTS positions (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID            NOT NULL REFERENCES broker_accounts(id),
    symbol          VARCHAR(30)     NOT NULL,
    market          VARCHAR(5)      NOT NULL,
    qty             INTEGER         NOT NULL DEFAULT 0,
    avg_cost        NUMERIC(14, 4)  NOT NULL DEFAULT 0,
    market_price    NUMERIC(14, 4),
    market_val      NUMERIC(18, 4),
    unrealized_pnl  NUMERIC(18, 4),
    realized_pnl    NUMERIC(18, 4)  NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, symbol, market)
);

-- 回测结果
CREATE TABLE IF NOT EXISTS backtest_results (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id     UUID            NOT NULL REFERENCES strategies(id),
    start_date      DATE            NOT NULL,
    end_date        DATE            NOT NULL,
    initial_capital NUMERIC(18, 4)  NOT NULL,
    final_equity    NUMERIC(18, 4),
    total_return    NUMERIC(10, 6),
    annual_return   NUMERIC(10, 6),
    sharpe_ratio    NUMERIC(8, 4),
    sortino_ratio   NUMERIC(8, 4),
    calmar_ratio    NUMERIC(8, 4),
    max_drawdown    NUMERIC(8, 6),
    win_rate        NUMERIC(6, 4),
    total_trades    INTEGER,
    status          VARCHAR(20)     NOT NULL DEFAULT 'pending',
    -- pending / running / completed / failed
    error_msg       TEXT,
    metrics         JSONB,          -- 完整指标JSON
    equity_curve    JSONB,          -- [{time, equity}, ...]
    trades          JSONB,          -- 交易明细列表
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- 风控配置
CREATE TABLE IF NOT EXISTS risk_configs (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID            NOT NULL REFERENCES users(id),
    name        VARCHAR(100)    NOT NULL DEFAULT 'default',
    rules       JSONB           NOT NULL DEFAULT '[]',
    is_active   BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 审计日志
CREATE TABLE IF NOT EXISTS audit_logs (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID            REFERENCES users(id),
    action      VARCHAR(50)     NOT NULL,   -- order.submit / strategy.start / etc.
    entity_type VARCHAR(50),
    entity_id   UUID,
    payload     JSONB,
    ip_addr     INET,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id, created_at DESC);

-- 告警记录
CREATE TABLE IF NOT EXISTS alerts (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID            REFERENCES users(id),
    strategy_id UUID            REFERENCES strategies(id),
    level       VARCHAR(10)     NOT NULL,   -- info / warning / critical
    title       VARCHAR(200)    NOT NULL,
    message     TEXT,
    is_read     BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);
