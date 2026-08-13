"""模型库。

- `template`        — 统一因子模型接口 `AlphaModelTemplate`（V4 · M5）
- `linear`          — Lasso 因子模型
- `boosting`        — 直方图式梯度提升因子模型（LightGBM 的无依赖替身）
- `legacy_adapters` — 收编既有三套 ML 实现的适配层
- `sequence`        — 序列模型 (B8)：LSTM / GRU / ALSTM

PyTorch 为可选依赖，全部通过 lazy import 加载，未安装时不影响其他功能。
"""
