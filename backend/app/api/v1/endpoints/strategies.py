import ast
import textwrap
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter()

# Preset strategy source files directory
# File is at: app/api/v1/endpoints/strategies.py → parents[3] = app/
_PRESETS_DIR = Path(__file__).resolve().parents[3] / "strategy" / "presets"

StrategyStatus = Literal["draft", "backtesting", "paper", "live", "stopped", "error"]

# 内置预设策略列表 — Phase 2 实现具体策略类
# name = snake_case 策略 ID（前端路由用）; description = 中文显示名
PRESET_STRATEGIES = [
    {"name": "double_ma",          "description": "双均线趋势 — 短期均线穿越长期均线触发买卖信号"},
    {"name": "bollinger",          "description": "布林带均值回归 — 价格触碰通道边界时反向交易"},
    {"name": "macd",               "description": "MACD 动量 — 利用 MACD 柱与信号线交叉捕捉趋势"},
    {"name": "rsi_mean_reversion", "description": "RSI 均值回归 — 超买超卖区域的反向修复策略"},
    {"name": "momentum_rotation",  "description": "动量轮动 ETF — 持有近期表现最强的 ETF 组合"},
    {"name": "grid_trading",       "description": "网格交易 — 在价格区间内自动挂出买卖网格订单"},
    {"name": "pairs_trading",      "description": "配对统计套利 — 基于协整关系的多空配对策略"},
    {"name": "multi_factor",       "description": "多因子选股 — 综合价值/动量/质量因子排名选股"},
]

_PRESET_NAMES = {p["name"] for p in PRESET_STRATEGIES}

# Template shown when user starts a blank strategy
_BLANK_TEMPLATE = textwrap.dedent("""\
    \"\"\"自定义策略模板

    继承 StrategyBase，实现 on_bar() 编写核心逻辑。
    通过 ctx 访问行情数据、持仓和下单接口。
    \"\"\"
    from __future__ import annotations

    from app.strategy.base import StrategyBase
    from app.strategy.context import StrategyContext
    from app.strategy.indicators import sma, ema, rsi, macd, crossover, crossunder


    class MyStrategy(StrategyBase):
        name = "my_strategy"
        description = "我的自定义策略"

        def on_start(self, ctx: StrategyContext) -> None:
            \"\"\"策略启动时调用一次，可初始化状态变量。\"\"\"
            pass

        def on_bar(self, ctx: StrategyContext) -> None:
            \"\"\"每根 K 线推送时调用。

            可用接口:
              ctx.bar           — 当前 OHLCV 数据
              ctx.history       — 历史 DataFrame (含当前 bar)
              ctx.cash          — 可用现金
              ctx.qty           — 当前持仓数量
              ctx.position(sym) — 指定标的持仓
              ctx.buy(qty)      — 按市价买入
              ctx.sell(qty)     — 按市价卖出
              ctx.sell_all()    — 清仓
            \"\"\"
            df = ctx.history
            if len(df) < 31:
                return

            fast_ma = sma(df, 10)
            slow_ma = sma(df, 30)

            if crossover(fast_ma, slow_ma).iloc[-1] and ctx.qty == 0:
                qty = int(ctx.cash * 0.95 / ctx.bar.close)
                if qty > 0:
                    ctx.buy(qty)

            elif crossunder(fast_ma, slow_ma).iloc[-1] and ctx.qty > 0:
                ctx.sell_all()

        def on_stop(self, ctx: StrategyContext) -> None:
            \"\"\"策略结束时调用一次。\"\"\"
            pass
""")


class StrategyCreate(BaseModel):
    name: str
    description: str | None = None
    preset: str | None = None        # 使用预设策略
    code: str | None = None          # 或自定义代码
    config: dict = {}
    markets: list[str] = []


class StrategyResponse(BaseModel):
    id: UUID
    name: str
    status: StrategyStatus
    preset: str | None
    markets: list[str]
    config: dict


class ValidateRequest(BaseModel):
    code: str


class ValidateResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]


@router.get("/presets")
async def list_presets() -> list[dict]:
    """获取所有内置预设策略"""
    return PRESET_STRATEGIES


@router.get("/source/{name}")
async def get_strategy_source(name: str) -> dict:
    """获取预设策略或空白模板的源码。name='blank' 返回示例模板。"""
    if name == "blank":
        return {"name": "blank", "source": _BLANK_TEMPLATE}

    if name not in _PRESET_NAMES:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")

    src_file = _PRESETS_DIR / f"{name}.py"
    if not src_file.exists():
        raise HTTPException(status_code=404, detail=f"Source file for '{name}' not found")

    return {"name": name, "source": src_file.read_text(encoding="utf-8")}


@router.post("/validate", response_model=ValidateResponse)
async def validate_strategy(body: ValidateRequest) -> ValidateResponse:
    """
    验证策略代码的语法和结构。
    - 检查 Python 语法错误
    - 确认定义了继承 StrategyBase 的策略类
    - 确认实现了 on_bar() 方法
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Syntax check
    try:
        tree = ast.parse(body.code)
    except SyntaxError as e:
        return ValidateResponse(
            valid=False,
            errors=[f"语法错误 (行 {e.lineno}): {e.msg}"],
            warnings=[],
        )

    # 2. Check for StrategyBase subclass with on_bar
    strategy_classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            base_names = [
                (b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else "")
                for b in node.bases
            ]
            if "StrategyBase" in base_names:
                strategy_classes.append(node)

    if not strategy_classes:
        errors.append("未找到继承 StrategyBase 的策略类。请确保有 class YourStrategy(StrategyBase)。")

    for cls in strategy_classes:
        method_names = {n.name for n in ast.walk(cls) if isinstance(n, ast.FunctionDef)}
        if "on_bar" not in method_names:
            errors.append(f"策略类 '{cls.name}' 未实现 on_bar() 方法。")
        if "on_start" not in method_names:
            warnings.append(f"建议在 '{cls.name}' 中实现 on_start() 初始化状态变量。")
        if "on_stop" not in method_names:
            warnings.append(f"建议在 '{cls.name}' 中实现 on_stop() 进行收尾处理。")

    return ValidateResponse(valid=len(errors) == 0, errors=errors, warnings=warnings)


@router.get("", response_model=list[StrategyResponse])
async def list_strategies() -> list[StrategyResponse]:
    # TODO: 从数据库查询
    return []


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
async def create_strategy(body: StrategyCreate) -> StrategyResponse:
    if body.preset and body.preset not in _PRESET_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown preset: {body.preset}")
    if not body.preset and not body.code:
        raise HTTPException(status_code=400, detail="Either preset or code is required")
    # TODO: 持久化到数据库
    raise HTTPException(status_code=501, detail="Phase 2 implementation pending")


@router.post("/{strategy_id}/start")
async def start_strategy(
    strategy_id: UUID,
    gateway: str = "alpaca_paper",
) -> dict:
    # TODO Phase 3: 接入实盘引擎
    return {"status": "pending", "message": f"Strategy {strategy_id} queued for gateway {gateway}"}


@router.post("/{strategy_id}/stop")
async def stop_strategy(strategy_id: UUID) -> dict:
    # TODO Phase 3
    return {"status": "stopped", "strategy_id": str(strategy_id)}
