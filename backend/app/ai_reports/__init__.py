"""AI 报告（V3 Wave C-a）—— 个股研报（I4）+ 回测诊断（I5）

两者共享同一套骨架：**先把事实收成结构化快照，再让模型只在快照上作文**。
研报的 `sources` 与诊断的 `grade.findings` 都是「模型看到了什么」的完整留痕，
用户可以拿它逐条核对模型有没有瞎编 —— 这是这两个功能能被信任的唯一理由。

对外只暴露这些名字；提示词与解析细节对端点不可见。
"""

from app.ai_reports.common import DISCLAIMER, AIReportError
from app.ai_reports.contradiction import Contradiction, detect_contradictions
from app.ai_reports.diagnosis import BacktestDiagnosis, generate_backtest_diagnosis
from app.ai_reports.snapshot import (
    NewsSource,
    SnapshotError,
    StockSnapshot,
    build_stock_snapshot,
)
from app.ai_reports.stock_report import StockReport, generate_stock_report

__all__ = [
    "DISCLAIMER",
    "AIReportError",
    "BacktestDiagnosis",
    "Contradiction",
    "NewsSource",
    "SnapshotError",
    "StockReport",
    "StockSnapshot",
    "build_stock_snapshot",
    "detect_contradictions",
    "generate_backtest_diagnosis",
    "generate_stock_report",
]
