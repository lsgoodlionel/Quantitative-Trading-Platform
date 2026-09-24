
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库里公开可见的占位密钥。字段默认值与生产校验都必须引用这一个常量：
# 如果两处各写一份字面量，改了一处忘了另一处，校验就静默失效了。
INSECURE_SECRET_KEY_DEFAULT = "CHANGE_ME_IN_PRODUCTION_USE_RANDOM_64_CHARS"

# HS256 的密钥短于摘要长度（32 字节）就削弱了签名强度。
MIN_SECRET_KEY_LENGTH = 32

# `.env.example` 里的占位串是 `CHANGE_ME_RUN_make_gen-secret`，与上面的字段默认值
# 并不相同 —— 只比对默认值会漏掉「照抄了 .env.example 就上生产」这条最常见的路径。
# 长度检查目前恰好也能拦住它（29 < 32），但那是巧合：占位串一变长就失效了。
_PLACEHOLDER_MARKER = "CHANGE_ME"

# 报错里给出可直接执行的修复命令。只写「配置无效」等于让人去翻源码。
_GEN_SECRET_HINT = "make gen-secret  # 或 openssl rand -hex 32"


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

    # CORS — 逗号分隔的允许来源字符串（环境变量），内部转为列表
    # 开发默认: http://localhost:3000,http://localhost:5173
    # 生产示例: https://quantbot.example.com
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v: object) -> str:
        """接受逗号分隔字符串或 JSON 数组，统一返回逗号字符串供 _origins 解析。"""
        if isinstance(v, list):
            return ",".join(str(x) for x in v)
        return str(v)

    @property
    def origins_list(self) -> list[str]:
        """将 allowed_origins 字符串解析为列表（去除空白）。"""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    # 数据库
    database_url: str = "postgresql+asyncpg://quantbot:quantbot_dev@localhost:5432/quantbot"
    db_pool_size: int = 20
    db_max_overflow: int = 40

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_stream_maxlen: int = 10_000

    # JWT 认证
    secret_key: str = INSECURE_SECRET_KEY_DEFAULT
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

    # 本地数据归档（M1）
    # 默认关闭：归档是缓存，缓存出错的表现是「回测结果莫名其妙变了」，
    # 属于最难排查的一类问题。关闭时 DataService 行为与归档上线前完全一致。
    archive_enabled: bool = False
    archive_root: str = "./data/archive"

    # 用户策略热加载（O2）
    #
    # ⚠️ 打开这个开关意味着：`user_strategies_dir` 下的 .py 文件会被**以本服务进程
    # 的权限执行**。没有沙箱（做不到，见 app/strategy/resolver.py 的模块 docstring），
    # 所以默认关闭，且该目录的写权限必须由部署方自己收紧。
    # 路径只从这里读，不接受任何请求参数。
    user_strategies_enabled: bool = False
    user_strategies_dir: str = "./user_data/strategies"

    # 自适应再训练定时调度（M6）
    #
    # 默认关闭。一个无人值守、每周自己跑的重训任务，产出的是一串没人看过的模型
    # 和一笔算力账单；开启前请先手动跑通一次 POST /retrain/jobs 看指标。
    # ⚠️ 即使开启，产出也**只入库不上线** —— 替换线上模型永远是人工动作。
    retrain_schedule_enabled: bool = False
    #: 定时重训的标的清单（逗号分隔）。为空时定时任务跳过并记日志。
    retrain_symbols: str = ""
    retrain_market: str = "US"
    retrain_model_kind: str = "lasso"
    #: beat 的星期（0=周日）与小时，默认每周日 22:00
    retrain_schedule_day_of_week: int = 0
    retrain_schedule_hour: int = 22

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

    @model_validator(mode="after")
    def check_production_hardening(self) -> "Settings":
        """生产环境的跨字段校验：不合格就**在启动时失败**。

        为什么是启动失败而不是一条 WARNING：一条警告在容器日志里滚过去等于没有，
        而一个用公开默认密钥跑着的生产实例，是在持续签发任何人都能伪造的令牌。

        为什么只卡 production：development / staging 必须保持零配置可启动。
        把开发也卡住，人只会去改 `ENVIRONMENT` 绕过，这个控制反而彻底失效。
        """
        if self.environment != "production":
            return self

        problems: list[str] = []

        # ⚠️ 以下任何一条都不得把 secret_key 的实际值写进消息（日志会留存）。
        if (
            self.secret_key == INSECURE_SECRET_KEY_DEFAULT
            or _PLACEHOLDER_MARKER in self.secret_key.upper()
        ):
            problems.append(
                "SECRET_KEY 仍是仓库里公开可见的默认占位值 —— 任何人都能用它伪造 admin 令牌。\n"
                f"    修复：生成一个随机密钥并写入环境变量 SECRET_KEY：\n      {_GEN_SECRET_HINT}"
            )
        elif len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            problems.append(
                f"SECRET_KEY 长度不足：{len(self.secret_key)} 字符 < 要求的 "
                f"{MIN_SECRET_KEY_LENGTH} 字符。\n"
                f"    修复：\n      {_GEN_SECRET_HINT}"
            )

        origins = self.origins_list
        if not origins:
            problems.append(
                "ALLOWED_ORIGINS 为空 —— 浏览器端将无法调用本 API。\n"
                "    修复：ALLOWED_ORIGINS=https://your-domain.example.com"
            )
        elif "*" in origins:
            problems.append(
                "ALLOWED_ORIGINS 含通配符 '*' —— 与 allow_credentials=True 组合意味着"
                "任意站点都能带着用户凭证调用本 API。\n"
                "    修复：改为逐个列出真实域名，如 "
                "ALLOWED_ORIGINS=https://your-domain.example.com"
            )

        if self.debug:
            problems.append(
                "DEBUG=True —— 会向调用方泄露堆栈与 SQL 语句。\n"
                "    修复：DEBUG=false"
            )

        if problems:
            detail = "\n".join(f"  [{i}] {p}" for i, p in enumerate(problems, 1))
            raise ValueError(
                "ENVIRONMENT=production 下的配置校验未通过，拒绝启动：\n"
                f"{detail}\n"
                "  （如果这是本机开发，正确的做法是设置 ENVIRONMENT=development，"
                "而不是放宽以上任何一条。）"
            )
        return self


settings = Settings()
