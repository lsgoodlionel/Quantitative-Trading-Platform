from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # 应用
    app_name: str = "QuantBot"
    environment: str = "development"
    log_level: str = "INFO"
    debug: bool = False

    # 数据库
    database_url: str = "postgresql+asyncpg://quantbot:quantbot_dev@localhost:5432/quantbot"
    db_pool_size: int = 20
    db_max_overflow: int = 40

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_stream_maxlen: int = 10_000

    # JWT 认证
    secret_key: str = "CHANGE_ME_IN_PRODUCTION_USE_RANDOM_64_CHARS"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24小时

    # Alpaca（美股）
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True   # True = 沙盒，False = 实盘
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    # 富途（港股/美股）
    futu_host: str = "127.0.0.1"
    futu_port: int = 11111
    futu_trade_env: str = "SIMULATE"  # SIMULATE / REAL
    futu_unlock_pwd: str = ""         # 交易解锁密码

    # Interactive Brokers（预留）
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1

    # CTP 期货（预留，后期绑定账户）
    futures_enabled: bool = False
    ctp_broker_id: str = ""
    ctp_investor_id: str = ""
    ctp_password: str = ""
    ctp_td_address: str = ""
    ctp_md_address: str = ""

    # 告警通知
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notify_webhook_url: str = ""
    notify_email_smtp: str = ""
    notify_email_from: str = ""
    notify_email_to: str = ""

    # 监控
    prometheus_enabled: bool = True

    @field_validator("environment")
    @classmethod
    def validate_env(cls, v: str) -> str:
        if v not in ("development", "staging", "production"):
            raise ValueError(f"Invalid environment: {v}")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


settings = Settings()
