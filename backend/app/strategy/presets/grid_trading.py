"""网格交易策略（Grid Trading）

在价格区间内等间距设置买卖网格，价格下跌到网格线买入，上涨到网格线卖出。
适合震荡行情。

参数:
  grid_count   — 网格数量（默认 10）
  grid_range   — 相对中心价格的上下浮动范围（默认 0.1 = ±10%）
  qty_per_grid — 每格交易股数（默认 10）
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext


class GridTradingStrategy(StrategyBase):
    name = "grid_trading"
    description = "网格交易策略（震荡行情适用）"

    def on_start(self, ctx: StrategyContext) -> None:
        self._center_price: float | None = None
        self._grid_levels: list[float] = []
        self._last_grid_idx: int | None = None

    def on_bar(self, ctx: StrategyContext) -> None:
        grid_count = self.param("grid_count", 10)
        grid_range = self.param("grid_range", 0.1)
        qty_per_grid = self.param("qty_per_grid", 10)

        close = ctx.bar.close

        # 第一根 bar 建立网格中心和网格线
        if self._center_price is None:
            self._center_price = close
            step = close * grid_range * 2 / grid_count
            self._grid_levels = [
                close * (1 - grid_range) + i * step for i in range(grid_count + 1)
            ]
            self._last_grid_idx = self._find_grid_idx(close)
            return

        current_idx = self._find_grid_idx(close)
        if current_idx is None or self._last_grid_idx is None:
            return

        if current_idx < self._last_grid_idx:
            # 价格下跌穿越网格线 → 买入
            if ctx.cash >= close * qty_per_grid:
                ctx.buy(qty_per_grid)

        elif current_idx > self._last_grid_idx:
            # 价格上涨穿越网格线 → 卖出
            pos_qty = ctx.qty
            sell_qty = min(qty_per_grid, pos_qty)
            if sell_qty > 0:
                ctx.sell(sell_qty)

        self._last_grid_idx = current_idx

    def _find_grid_idx(self, price: float) -> int | None:
        for i, level in enumerate(self._grid_levels):
            if price < level:
                return i
        return len(self._grid_levels)
