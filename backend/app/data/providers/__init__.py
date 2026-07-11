"""标准化数据提供者层（Wave-2a / A1+A2）。

- base.Fetcher: 仿 OpenBB 的 transform_query→extract_data→transform_data 管道
- models: 标准基本面 Pydantic 模型
- {yfinance,akshare}_provider: 具体数据源适配
- service.FundamentalsService: 按市场路由 + 派生比率的统一入口
"""

from app.data.providers.service import FundamentalsService

__all__ = ["FundamentalsService"]
