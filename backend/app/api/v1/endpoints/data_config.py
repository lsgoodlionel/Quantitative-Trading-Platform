"""
数据通道状态 API — 查询各市场数据源的安装和连通状态

各市场数据源优先级:
  A股:  AkShare（免费，日/周线）→ 合成演示数据兜底
  港股:  Futu OpenAPI（需要OpenD）→ yfinance（备用）→ 合成演示数据兜底
  美股:  Alpaca（实时+历史）→ yfinance（备用）→ 合成演示数据兜底
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class FeedStatus(BaseModel):
    name: str                          # 数据源名称
    kind: Literal["primary", "fallback", "demo"]
    installed: bool
    version: str | None = None
    ok: bool                           # 连通/可用
    error: str | None = None
    note: str | None = None            # 说明文字（免费/需要账号等）


class MarketDataStatus(BaseModel):
    market: str                        # "A" / "HK" / "US"
    label: str
    feeds: list[FeedStatus]
    realtime: bool                     # 是否支持实时行情


class DataConfigStatusResponse(BaseModel):
    a_share: MarketDataStatus
    hk: MarketDataStatus


# ── 探针逻辑 ──────────────────────────────────────────────────────────────────

def _check_package(pkg_name: str) -> tuple[bool, str | None]:
    """检查包是否可导入，返回 (installed, version)。"""
    try:
        mod = importlib.import_module(pkg_name)
        ver = getattr(mod, "__version__", None)
        return True, ver
    except ImportError:
        return False, None


async def _probe_akshare() -> FeedStatus:
    """探测 AkShare 是否可用（尝试拉取上证指数最近1条日线）。"""
    installed, version = _check_package("akshare")
    if not installed:
        return FeedStatus(
            name="AkShare",
            kind="primary",
            installed=False,
            ok=False,
            error="未安装：pip install akshare",
            note="免费接口，支持沪深 A 股日/周线历史数据，无需 API Key",
        )

    def _fetch() -> None:
        import akshare as ak
        # 用上证指数日线做轻量探测（比个股接口稳定）
        ak.stock_zh_index_daily(symbol="sh000001")

    try:
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(loop.run_in_executor(None, _fetch), timeout=8.0)
        return FeedStatus(
            name="AkShare",
            kind="primary",
            installed=True,
            version=version,
            ok=True,
            note="免费接口，支持沪深 A 股日/周线历史数据，无需 API Key",
        )
    except TimeoutError:
        return FeedStatus(
            name="AkShare",
            kind="primary",
            installed=True,
            version=version,
            ok=False,
            error="连接超时（网络不稳定或被限速）",
            note="免费接口，支持沪深 A 股日/周线历史数据，无需 API Key",
        )
    except Exception as e:
        err = str(e)
        # 缩短超长错误信息
        if len(err) > 120:
            err = err[:120] + "…"
        return FeedStatus(
            name="AkShare",
            kind="primary",
            installed=True,
            version=version,
            ok=False,
            error=err,
            note="免费接口，支持沪深 A 股日/周线历史数据，无需 API Key",
        )


async def _probe_futu() -> FeedStatus:
    """探测 Futu OpenAPI：先检查 futu-api 包，再尝试连接 OpenD。"""
    installed, version = _check_package("futu")
    if not installed:
        return FeedStatus(
            name="富途 OpenAPI",
            kind="primary",
            installed=False,
            ok=False,
            error="未安装：pip install futu-api",
            note=f"需要 futu-api 包 + 本地 OpenD 程序（{settings.futu_host}:{settings.futu_port}）",
        )

    # 包已安装，尝试连接 OpenD
    def _connect() -> None:
        from futu import OpenQuoteContext
        ctx = OpenQuoteContext(host=settings.futu_host, port=settings.futu_port)
        ctx.close()

    try:
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(loop.run_in_executor(None, _connect), timeout=5.0)
        return FeedStatus(
            name="富途 OpenAPI",
            kind="primary",
            installed=True,
            version=version,
            ok=True,
            note=f"OpenD 已连接（{settings.futu_host}:{settings.futu_port}），支持港股实时行情",
        )
    except TimeoutError:
        return FeedStatus(
            name="富途 OpenAPI",
            kind="primary",
            installed=True,
            version=version,
            ok=False,
            error=f"OpenD 连接超时（{settings.futu_host}:{settings.futu_port}），请确认 OpenD 正在运行",
            note=f"需要本地 OpenD 程序监听 {settings.futu_host}:{settings.futu_port}",
        )
    except Exception as e:
        err = str(e)
        if "Connection refused" in err or "Connect call failed" in err:
            err = f"OpenD 未运行（{settings.futu_host}:{settings.futu_port}），请启动 OpenD 桌面程序"
        elif len(err) > 120:
            err = err[:120] + "…"
        return FeedStatus(
            name="富途 OpenAPI",
            kind="primary",
            installed=True,
            version=version,
            ok=False,
            error=err,
            note=f"需要本地 OpenD 程序监听 {settings.futu_host}:{settings.futu_port}",
        )


async def _probe_yfinance(market_label: str) -> FeedStatus:
    """探测 yfinance 备用数据源。"""
    installed, version = _check_package("yfinance")
    if not installed:
        return FeedStatus(
            name="yfinance",
            kind="fallback",
            installed=False,
            ok=False,
            error="未安装：pip install yfinance",
            note=f"免费{market_label}历史数据备用通道，无需 API Key",
        )

    def _fetch() -> None:
        import yfinance as yf
        t = yf.Ticker("^HSI")
        hist = t.history(period="1d", interval="1d")
        if hist.empty:
            raise RuntimeError("yfinance 返回空数据")

    try:
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(loop.run_in_executor(None, _fetch), timeout=10.0)
        return FeedStatus(
            name="yfinance",
            kind="fallback",
            installed=True,
            version=version,
            ok=True,
            note=f"免费{market_label}历史数据备用通道，无需 API Key",
        )
    except TimeoutError:
        return FeedStatus(
            name="yfinance",
            kind="fallback",
            installed=True,
            version=version,
            ok=False,
            error="连接超时",
            note=f"免费{market_label}历史数据备用通道，无需 API Key",
        )
    except Exception as e:
        err = str(e)
        if "Too Many Requests" in err or "Rate" in err:
            err = "请求频率超限（短暂限速，稍后自动恢复）"
        elif len(err) > 100:
            err = err[:100] + "…"
        return FeedStatus(
            name="yfinance",
            kind="fallback",
            installed=True,
            version=version,
            ok=False,
            error=err,
            note=f"免费{market_label}历史数据备用通道，无需 API Key",
        )


# ── 端点 ─────────────────────────────────────────────────────────────────────

@router.get("/status", response_model=DataConfigStatusResponse)
async def get_data_config_status() -> DataConfigStatusResponse:
    """
    并行探测 A股 / 港股数据通道状态。

    每次请求实时探测（带超时），结果反映当前真实状态。
    美股通道状态通过 /broker-config/alpaca/test 单独查询。
    """
    # 并行探测所有数据源，降低总延迟
    akshare_task    = asyncio.create_task(_probe_akshare())
    futu_task       = asyncio.create_task(_probe_futu())
    yfinance_task   = asyncio.create_task(_probe_yfinance("港股"))

    akshare_status, futu_status, yfinance_hk_status = await asyncio.gather(
        akshare_task, futu_task, yfinance_task
    )

    demo_a = FeedStatus(
        name="合成演示数据",
        kind="demo",
        installed=True,
        ok=True,
        note="GBM 模型生成模拟行情，所有真实数据源失败时自动兜底",
    )

    demo_hk = FeedStatus(
        name="合成演示数据",
        kind="demo",
        installed=True,
        ok=True,
        note="GBM 模型生成模拟行情，所有真实数据源失败时自动兜底",
    )

    return DataConfigStatusResponse(
        a_share=MarketDataStatus(
            market="A",
            label="沪深 A 股",
            feeds=[akshare_status, demo_a],
            realtime=False,
        ),
        hk=MarketDataStatus(
            market="HK",
            label="港股",
            feeds=[futu_status, yfinance_hk_status, demo_hk],
            realtime=futu_status.ok,
        ),
    )
