"""
通知配置模型（Pydantic）

多渠道（Telegram / Webhook）出站通知配置，存储于 Redis（notify:config）。
密钥明文存储，读取时经 *_status 模型脱敏，绝不返回完整密钥。
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ChannelType(str, Enum):
    TELEGRAM = "telegram"
    WEBHOOK = "webhook"


class NotifyEventType(str, Enum):
    TRADE_FILL = "trade_fill"
    ORDER_REJECT = "order_reject"
    PNL_UPDATE = "pnl_update"
    POSITION = "position"
    DAILY_SUMMARY = "daily_summary"
    RISK_ALERT = "risk_alert"
    PROTECTION = "protection"


class WebhookFormat(str, Enum):
    JSON = "json"
    FORM = "form"
    RAW = "raw"


# ── 请求模型（PUT 提交） ──────────────────────────────────────

class TelegramChannelConfig(BaseModel):
    bot_token: str = Field(default="", description="Telegram bot token（写入，脱敏读取）")
    chat_id: str = Field(min_length=1)
    parse_mode: Literal["HTML", "Markdown"] = "HTML"


class WebhookChannelConfig(BaseModel):
    url: str = Field(min_length=1)
    format: WebhookFormat = WebhookFormat.JSON
    timeout_seconds: int = Field(default=10, ge=1, le=60)
    retries: int = Field(default=2, ge=0, le=10)
    retry_delay_seconds: float = Field(default=1.0, ge=0, le=30)
    secret_header: Optional[str] = None
    secret_value: Optional[str] = None


class ChannelConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: ChannelType
    name: str = Field(default="", max_length=60)
    enabled: bool = True
    events: list[NotifyEventType] = Field(default_factory=list)
    telegram: Optional[TelegramChannelConfig] = None
    webhook: Optional[WebhookChannelConfig] = None

    @model_validator(mode="after")
    def _check_channel_body(self) -> "ChannelConfig":
        if self.type == ChannelType.TELEGRAM:
            if self.telegram is None or self.webhook is not None:
                raise ValueError("telegram channel requires 'telegram' body only")
        elif self.type == ChannelType.WEBHOOK:
            if self.webhook is None or self.telegram is not None:
                raise ValueError("webhook channel requires 'webhook' body only")
        return self

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class NotifyConfig(BaseModel):
    is_active: bool = True
    channels: list[ChannelConfig] = Field(default_factory=list)
    min_pnl_notify_abs: float = Field(default=0.0, ge=0)
    daily_summary_time: str = Field(default="16:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

    def get_channel(self, channel_id: str) -> Optional[ChannelConfig]:
        for ch in self.channels:
            if ch.id == channel_id:
                return ch
        return None


# ── 响应模型（GET 返回，脱敏） ────────────────────────────────

class TelegramChannelStatus(BaseModel):
    configured: bool
    token_hint: Optional[str] = None
    chat_id: str
    parse_mode: Literal["HTML", "Markdown"]


class WebhookChannelStatus(BaseModel):
    url: str
    format: WebhookFormat
    timeout_seconds: int
    retries: int
    retry_delay_seconds: float
    has_secret: bool


class ChannelStatus(BaseModel):
    id: str
    type: ChannelType
    name: str
    enabled: bool
    events: list[NotifyEventType]
    telegram: Optional[TelegramChannelStatus] = None
    webhook: Optional[WebhookChannelStatus] = None


class NotifyConfigStatus(BaseModel):
    is_active: bool
    channels: list[ChannelStatus]
    min_pnl_notify_abs: float
    daily_summary_time: str


# ── 测试端点模型 ──────────────────────────────────────────────

class NotifyTestRequest(BaseModel):
    channel_id: str
    event_type: NotifyEventType = NotifyEventType.TRADE_FILL


class NotifyTestResponse(BaseModel):
    ok: bool
    channel_id: str
    detail: Optional[str] = None
    error: Optional[str] = None


# ── 脱敏 / 转换工具 ───────────────────────────────────────────

def mask_secret(value: str) -> str:
    """显示首2位 + 掩码 + 末2位，如 12••••cd。"""
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return value[:2] + "••••" + value[-2:]


def to_status(config: NotifyConfig) -> NotifyConfigStatus:
    """将完整配置转为脱敏状态模型。"""
    channels: list[ChannelStatus] = []
    for ch in config.channels:
        tg_status: Optional[TelegramChannelStatus] = None
        wh_status: Optional[WebhookChannelStatus] = None
        if ch.telegram is not None:
            tg = ch.telegram
            tg_status = TelegramChannelStatus(
                configured=bool(tg.bot_token),
                token_hint=mask_secret(tg.bot_token) if tg.bot_token else None,
                chat_id=tg.chat_id,
                parse_mode=tg.parse_mode,
            )
        if ch.webhook is not None:
            wh = ch.webhook
            wh_status = WebhookChannelStatus(
                url=wh.url,
                format=wh.format,
                timeout_seconds=wh.timeout_seconds,
                retries=wh.retries,
                retry_delay_seconds=wh.retry_delay_seconds,
                has_secret=bool(wh.secret_value),
            )
        channels.append(
            ChannelStatus(
                id=ch.id,
                type=ch.type,
                name=ch.name,
                enabled=ch.enabled,
                events=ch.events,
                telegram=tg_status,
                webhook=wh_status,
            )
        )
    return NotifyConfigStatus(
        is_active=config.is_active,
        channels=channels,
        min_pnl_notify_abs=config.min_pnl_notify_abs,
        daily_summary_time=config.daily_summary_time,
    )


def default_notify_config() -> NotifyConfig:
    """默认通知配置：启用但无渠道（配置渠道前不发送）。"""
    return NotifyConfig(is_active=True, channels=[])
