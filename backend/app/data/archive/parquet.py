"""
Parquet 归档后端（M1）

⚠️ 现实约束：本项目 requirements 里**没有** parquet 引擎（pyarrow / fastparquet），
而 M-a 的红线之一是「不新增依赖」。因此本类在引擎缺失时会在构造期抛
`ParquetEngineUnavailableError`，由 `create_archive()` 自动回退到 `NpzArchive`。
一旦项目引入 pyarrow，工厂会自动改用本类，无需改调用方代码。

不在这里静默降级成别的格式：`.parquet` 后缀的文件必须真的是 parquet，
否则外部工具读它时会得到一个说不清的错误。
"""

from __future__ import annotations

from pathlib import Path

from app.data.archive.base import ArchiveError
from app.data.archive.columns import COLUMNS
from app.data.archive.file_store import FileArchiveBase

_ENGINES = ("pyarrow", "fastparquet")


class ParquetEngineUnavailableError(ArchiveError):
    """环境里没有可用的 parquet 引擎。"""


def available_engine() -> str | None:
    """返回第一个可用的 parquet 引擎名；都没有返回 None。"""
    import importlib.util

    return next((name for name in _ENGINES if importlib.util.find_spec(name) is not None), None)


class ParquetArchive(FileArchiveBase):
    """一个 (symbol, market, frequency) 一个 `.parquet` 文件。"""

    extension = ".parquet"

    def __init__(self, root: str | Path) -> None:
        engine = available_engine()
        if engine is None:
            raise ParquetEngineUnavailableError(
                "未安装 parquet 引擎（pyarrow / fastparquet），无法使用 ParquetArchive"
            )
        self._engine = engine
        super().__init__(root)

    def _load(self, path: Path) -> dict[str, list]:
        import pandas as pd

        frame = pd.read_parquet(path, engine=self._engine)
        return {col: frame[col].tolist() for col in COLUMNS if col in frame.columns}

    def _dump(self, path: Path, data: dict[str, list]) -> None:
        import pandas as pd

        frame = pd.DataFrame({col: data.get(col, []) for col in COLUMNS})
        frame.to_parquet(path, engine=self._engine, index=False)
