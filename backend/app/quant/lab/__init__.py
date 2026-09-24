"""投研产物库（V4 · M4）：数据集 / 模型 / 信号的持久化。

元数据落 TimescaleDB，内容落文件系统，无容量上限、仅手动删除。
反序列化 pickle 的安全边界见 `base.py` 模块 docstring —— 改动前必读。
"""

from app.quant.lab.base import (
    CAPACITY_POLICY,
    ArtifactChecksumError,
    ArtifactFilter,
    ArtifactKind,
    ArtifactKindMismatchError,
    ArtifactMeta,
    ArtifactNotFoundError,
    InvalidArtifactIdError,
    LabError,
    new_artifact_id,
    validate_artifact_id,
)
from app.quant.lab.content import FileSystemContentStore, default_lab_root
from app.quant.lab.metadata import PostgresArtifactMetaStore
from app.quant.lab.store import LabStore

__all__ = [
    "CAPACITY_POLICY",
    "ArtifactChecksumError",
    "ArtifactFilter",
    "ArtifactKind",
    "ArtifactKindMismatchError",
    "ArtifactMeta",
    "ArtifactNotFoundError",
    "FileSystemContentStore",
    "InvalidArtifactIdError",
    "LabError",
    "LabStore",
    "PostgresArtifactMetaStore",
    "default_lab_root",
    "new_artifact_id",
    "validate_artifact_id",
]
