"""
NPZ 归档后端（M1）— 零新增依赖的默认实现

只用 numpy（已是既有依赖）的 `savez_compressed` / `load`，一个 key 一个 `.npz` 文件，
每列一个数组。语义与 parquet 后端完全一致，只是容器格式不同。

存在理由见 `parquet.py` 的模块说明：环境中没有 parquet 引擎，
而红线要求不新增依赖 —— 与其让归档功能整体不可用，不如换一个同样列式、
同样压缩、且不需要新依赖的容器。
"""

from __future__ import annotations

from pathlib import Path

from app.data.archive.columns import COLUMNS, STRING_COLUMNS
from app.data.archive.file_store import FileArchiveBase

# numpy 无法表达 None，可空数值列统一用 NaN 占位（读回时由 columns._cell 还原为 None）
_NAN = float("nan")


class NpzArchive(FileArchiveBase):
    """一个 (symbol, market, frequency) 一个 `.npz` 文件。"""

    extension = ".npz"

    def _load(self, path: Path) -> dict[str, list]:
        import numpy as np

        with np.load(path, allow_pickle=False) as payload:
            return {col: payload[col].tolist() for col in COLUMNS if col in payload.files}

    def _dump(self, path: Path, data: dict[str, list]) -> None:
        import numpy as np

        # 字符串列（time / asset_class）必须按 str 落盘：走 float64 会直接抛
        # ValueError，或者更糟 —— 把枚举名悄悄变成 nan。
        arrays = {
            col: np.asarray(data.get(col, []), dtype=np.str_)
            for col in COLUMNS
            if col in STRING_COLUMNS
        }
        for col in COLUMNS:
            if col in STRING_COLUMNS:
                continue
            values = [_NAN if v is None else float(v) for v in data.get(col, [])]
            arrays[col] = np.asarray(values, dtype=np.float64)

        # 必须传文件对象而不是路径：`savez_compressed` 收到路径时会给不以 `.npz`
        # 结尾的文件名自动补后缀，而写入走的是临时文件（`xxx.npz.tmp`），
        # 传路径会写成 `xxx.npz.tmp.npz`，后面的原子 rename 就找不到它了。
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
