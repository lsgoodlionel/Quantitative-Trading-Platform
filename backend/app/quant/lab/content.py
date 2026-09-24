"""
投研产物库 —— 文件系统内容存储（V4 · M4）

内容（数据集 / 模型 / 信号的 pickle 字节流）落本地目录，
**不进数据库** —— 把模型二进制塞进 JSONB 会让备份、复制、查询全部变形。

目录布局（扁平，按 UUID 前两位分桶避免单目录文件过多）::

    {root}/ab/abcdef12-....pkl

`artifact_id` 进来先过 `validate_artifact_id()`：必须是 UUID，
所以路径永远是服务端可控的，用户给的 `name` 不参与拼接。
"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.logging import get_logger
from app.quant.lab.base import (
    CONTENT_SUFFIX,
    ArtifactNotFoundError,
    LabError,
    validate_artifact_id,
)

logger = get_logger(__name__)

# 内容根目录环境变量；未配置时落在 backend/data/lab 下
LAB_ROOT_ENV = "QUANTBOT_LAB_ROOT"
DEFAULT_LAB_DIRNAME = "data/lab"

# UUID 前 N 位作为分桶目录名
_BUCKET_WIDTH = 2


def default_lab_root() -> Path:
    """产物内容根目录：优先环境变量，否则 `backend/data/lab`。"""
    configured = os.environ.get(LAB_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    # 本文件位于 backend/app/quant/lab/content.py → 上溯四级即 backend/
    backend_root = Path(__file__).resolve().parents[3]
    return backend_root / DEFAULT_LAB_DIRNAME


class FileSystemContentStore:
    """把产物内容写到本地目录的 `ArtifactContentStore` 实现。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root is not None else default_lab_root()

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, artifact_id: str) -> Path:
        """产物内容的绝对路径（ID 校验失败会抛 `InvalidArtifactIdError`）。"""
        valid_id = validate_artifact_id(artifact_id)
        return self._root / valid_id[:_BUCKET_WIDTH] / f"{valid_id}{CONTENT_SUFFIX}"

    def write(self, artifact_id: str, payload: bytes) -> int:
        """写入内容，返回字节数。同 ID 覆盖写（ID 是 UUID，实际不会撞）。"""
        path = self.path_for(artifact_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 先写临时文件再原子改名：写一半崩溃不会留下半截产物
            tmp_path = path.with_suffix(f"{CONTENT_SUFFIX}.tmp")
            tmp_path.write_bytes(payload)
            tmp_path.replace(path)
        except OSError as exc:
            raise LabError(f"产物内容写入失败 ({artifact_id}): {exc}") from exc
        logger.info("Lab artifact content written", artifact_id=artifact_id, size=len(payload))
        return len(payload)

    def read(self, artifact_id: str) -> bytes:
        """读取内容；文件缺失抛 `ArtifactNotFoundError`。"""
        path = self.path_for(artifact_id)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactNotFoundError(f"产物内容文件不存在: {artifact_id}") from exc
        except OSError as exc:
            raise LabError(f"产物内容读取失败 ({artifact_id}): {exc}") from exc

    def delete(self, artifact_id: str) -> bool:
        """删除内容文件，返回是否曾存在。"""
        path = self.path_for(artifact_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise LabError(f"产物内容删除失败 ({artifact_id}): {exc}") from exc
        return True

    def exists(self, artifact_id: str) -> bool:
        return self.path_for(artifact_id).is_file()
