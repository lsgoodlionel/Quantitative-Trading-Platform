"""
XTP A股实盘网关存根（中泰证券 XTP 协议）

参考:
  github.com/nickcoutsos/python-xtp  — Python XTP 绑定
  XTP 官方 SDK: https://xtp.zts.com.cn/

XTP（X-TRADER Platform）是中泰证券提供的开源高性能交易系统协议，
支持 A股股票、ETF、基金等产品的极速交易。

当前状态: 存根实现（Stub）
  - 基础接口已定义，对接 OMS 不中断启动
  - 实盘接入需安装 XTP SDK: pip install xtp-python
  - 填写 ctp_broker_id / ctp_investor_id / ctp_password 等环境变量

后续完整实现步骤:
  1. 申请中泰证券 XTP 开发账户
  2. pip install xtp-python（仅 Linux/Windows 支持）
  3. 实现 _init_xtp_api() 和完整成交回调
  4. 参考: github.com/openctp/openctp 做 CTP → XTP 映射
"""

from __future__ import annotations

import logging

from app.gateway.base import AccountInfo, BrokerPosition, TradingGateway
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType

logger = logging.getLogger(__name__)


class XTPGateway(TradingGateway):
    """
    XTP A股实盘网关。

    当前为存根（Stub）实现：
    - 所有操作均抛出 NotImplementedError，提示需要配置
    - OMS 自动降级到 PaperGateway（纸面交易）

    切换到真实 XTP 接口步骤:
    1. 安装 XTP SDK: pip install xtp-python
    2. 填写环境变量:
       XTP_BROKER_ID=<中泰证券经纪商 ID>
       XTP_INVESTOR_ID=<账户>
       XTP_PASSWORD=<密码>
       XTP_TD_ADDRESS=<交易服务器地址>
       XTP_QUOTE_ADDRESS=<行情服务器地址>
    3. 将本类替换为完整实现
    """

    _STUB_MESSAGE = (
        "XTP A股网关为存根（Stub）实现，需要配置中泰证券 XTP SDK。"
        "当前 A股交易通过 PaperGateway 模拟执行。"
        "参考: https://github.com/openctp/openctp"
    )

    def __init__(self) -> None:
        self._connected = False

    async def connect(self) -> None:
        logger.warning("XTPGateway: %s", self._STUB_MESSAGE)
        # Stub: 模拟连接成功，实际不连接 XTP 服务器
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def submit_order(self, order: LiveOrder) -> str:
        raise NotImplementedError(self._STUB_MESSAGE)

    async def cancel_order(self, broker_order_id: str) -> None:
        raise NotImplementedError(self._STUB_MESSAGE)

    async def get_order(self, broker_order_id: str) -> dict:
        raise NotImplementedError(self._STUB_MESSAGE)

    async def get_open_orders(self) -> list[dict]:
        return []

    async def get_account(self) -> AccountInfo:
        return AccountInfo(
            account_id="XTP-STUB",
            currency="CNY",
            cash=0.0,
            buying_power=0.0,
            portfolio_value=0.0,
        )

    async def get_positions(self) -> list[BrokerPosition]:
        return []
