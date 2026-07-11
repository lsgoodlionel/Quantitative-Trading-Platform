"""
标准化数据提供者接口 — 仿 OpenBB Fetcher（Wave-2a / A1）

设计参考: refs/OpenBB/.../provider/abstract/fetcher.py

三段式数据管道（与 OpenBB 一致）:
  transform_query : 原始 dict 参数 → 强类型 QueryParams
  extract_data    : QueryParams → 提供者原始数据（可为 async）
  transform_data  : 原始数据 → 标准 Pydantic 模型（List[Data] 或 Data）

与 OpenBB 的差异（KISS）:
  - 不依赖 credentials 注入框架；凭据由各数据源自行惰性读取
  - extract_data 统一在线程池执行（yfinance / akshare 均为阻塞 IO）
  - 仅保留平台实际需要的能力，避免 OpenBB 的 registry / provider 元编程

新增一个数据源 = 定义 QueryParams + Data 模型 + 一个 Fetcher 子类。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

Q = TypeVar("Q", bound="QueryParams")
R = TypeVar("R")


class QueryParams(BaseModel):
    """所有查询参数的基类（强类型、可校验）。"""

    model_config = ConfigDict(extra="forbid")


class Data(BaseModel):
    """所有标准数据模型的基类。

    extra="allow" — 保留提供者返回但标准模型未显式声明的字段，
    避免因数据源多返回字段而丢信息（与 OpenBB Data 行为一致）。
    """

    model_config = ConfigDict(extra="allow")


class Fetcher(Generic[Q, R]):
    """抽象 Fetcher。子类实现三段式管道中的三个静态方法。"""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> Q:
        """原始参数 dict → 提供者专用 QueryParams。"""
        raise NotImplementedError

    @staticmethod
    def extract_data(query: Q) -> Any:
        """从提供者提取原始数据（同步或 async，均可）。"""
        raise NotImplementedError

    @staticmethod
    def transform_data(query: Q, data: Any) -> R:
        """提供者原始数据 → 标准模型。"""
        raise NotImplementedError

    @classmethod
    async def fetch_data(cls, params: dict[str, Any]) -> R:
        """执行完整管道，返回标准模型。

        extract_data 若为同步函数（阻塞 IO），自动放入线程池执行，
        避免阻塞 FastAPI 事件循环。
        """
        query = cls.transform_query(params)
        if inspect.iscoroutinefunction(cls.extract_data):
            raw = await cls.extract_data(query)
        else:
            raw = await asyncio.to_thread(cls.extract_data, query)
        return cls.transform_data(query, raw)
