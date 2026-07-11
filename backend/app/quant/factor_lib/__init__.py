"""
声明式因子库（Declarative Factor Library）— v2.0 Wave 2 / Epic B（B2 + B3 支撑）

子模块：
  operators.py  — 纯 pandas/numpy 滚动算子原语（Slope/Rsquare/Resi/Corr/Cov/
                  WMA/EMA/Quantile/Mad/IdxMax/IdxMin/Rank），供公式引擎与因子库共享
  loader.py     — 配置 → Alpha158 式因子表达式生成（字段 × 算子 × 窗口）
  ranking.py    — 因子库横截面 IC 批量排行

为避免导入期开销与循环依赖，本包 __init__ 不主动导入任何子模块；
调用方按需 `from app.quant.factor_lib.loader import ...`。
"""
