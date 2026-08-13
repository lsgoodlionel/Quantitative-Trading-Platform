"""
投研产物库 —— 存取门面（V4 · M4）

对外只暴露这一个类：`LabStore`。它把两件事缝在一起 ——
元数据走 `ArtifactMetaStore`（TimescaleDB），内容走 `ArtifactContentStore`（文件系统）。

⚠️ `load_model` 会反序列化 pickle，等于执行任意代码。三道约束见
`app/quant/lab/base.py` 模块 docstring；其中「加载前校验 checksum」
就落在本文件的 `_load()` 里 —— **不要**为了性能把那次校验去掉。
**不得加载外部来源的产物。**

容量：无上限，只支持手动 `delete()`（`base.CAPACITY_POLICY`）。
实验记录器（`app/quant/experiments/recorder.py`）的 `MAX_RECORDS` 滚动淘汰
不影响这里 —— 实验被淘汰了，它引用的产物照样加载得到，这正是 M4 存在的理由。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.quant.lab.base import (
    ArtifactChecksumError,
    ArtifactContentStore,
    ArtifactFilter,
    ArtifactKind,
    ArtifactKindMismatchError,
    ArtifactMeta,
    ArtifactMetaStore,
    ArtifactNotFoundError,
    LabError,
    compute_checksum,
    deserialize,
    new_artifact_id,
    normalize_tags,
    serialize,
    validate_artifact_id,
)

logger = get_logger(__name__)

# 列表默认页大小
DEFAULT_LIST_LIMIT = 50


class LabStore:
    """投研产物库：数据集 / 模型 / 信号的存 → 列 → 读 → 删。"""

    def __init__(self, meta_store: ArtifactMetaStore, content_store: ArtifactContentStore) -> None:
        self._meta = meta_store
        self._content = content_store

    # ── 写入 ──────────────────────────────────────────────────────

    async def save_dataset(
        self, name: str, dataset: pd.DataFrame, tags: dict[str, Any] | None = None
    ) -> ArtifactMeta:
        """存一份特征/标签数据集。"""
        if not isinstance(dataset, pd.DataFrame):
            raise TypeError("dataset 必须是 pandas.DataFrame")
        return await self._save(ArtifactKind.DATASET, name, dataset, tags)

    async def save_model(
        self, name: str, model: Any, tags: dict[str, Any] | None = None
    ) -> ArtifactMeta:
        """存一个训练好的模型（通常是 `AlphaModelTemplate` 子类实例）。"""
        return await self._save(ArtifactKind.MODEL, name, model, tags)

    async def save_signal(
        self, name: str, signal: pd.Series, tags: dict[str, Any] | None = None
    ) -> ArtifactMeta:
        """存一条预测信号序列。"""
        if not isinstance(signal, pd.Series):
            raise TypeError("signal 必须是 pandas.Series")
        return await self._save(ArtifactKind.SIGNAL, name, signal, tags)

    async def _save(
        self, kind: ArtifactKind, name: str, obj: Any, tags: dict[str, Any] | None
    ) -> ArtifactMeta:
        """
        统一写入路径：先落内容再落元数据。

        `artifact_id` 由服务端生成 UUID —— 用户给的 `name` 只是标签，
        哪怕它是 `../../etc/passwd` 也碰不到文件路径。
        """
        artifact_id = new_artifact_id()
        payload = serialize(obj)
        size_bytes = self._content.write(artifact_id, payload)

        meta = ArtifactMeta(
            artifact_id=artifact_id,
            kind=kind,
            name=str(name),
            created_at=datetime.now(UTC),
            size_bytes=size_bytes,
            tags=normalize_tags(tags),
            checksum=compute_checksum(payload),
        )
        try:
            return await self._meta.save(meta)
        except Exception:
            # 元数据落库失败则回滚内容文件，避免留下查不到的孤儿文件。
            # 回滚本身再失败也不能吞掉原始异常 —— 那才是调用方要看到的原因。
            try:
                self._content.delete(artifact_id)
            except LabError as cleanup_exc:
                logger.error(
                    "Lab content rollback failed",
                    artifact_id=artifact_id,
                    error=str(cleanup_exc),
                )
            logger.error("Lab meta save failed, content rolled back", artifact_id=artifact_id)
            raise

    # ── 读取 ──────────────────────────────────────────────────────

    async def load_dataset(self, artifact_id: str) -> pd.DataFrame:
        return await self._load(artifact_id, ArtifactKind.DATASET)

    async def load_model(self, artifact_id: str) -> Any:
        """
        加载模型产物。

        ⚠️ 反序列化 pickle = 执行任意代码。仅限本服务自产的产物；
        checksum 不匹配（文件被篡改）会直接拒绝。
        """
        return await self._load(artifact_id, ArtifactKind.MODEL)

    async def load_signal(self, artifact_id: str) -> pd.Series:
        return await self._load(artifact_id, ArtifactKind.SIGNAL)

    async def _load(self, artifact_id: str, expected_kind: ArtifactKind) -> Any:
        """统一读取路径：元数据 → 类别校验 → 内容 → **checksum 校验** → 反序列化。"""
        valid_id = validate_artifact_id(artifact_id)
        meta = await self._meta.get(valid_id)
        if meta is None:
            raise ArtifactNotFoundError(f"产物不存在: {valid_id}")
        if meta.kind is not expected_kind:
            raise ArtifactKindMismatchError(
                f"产物 {valid_id} 的类别是 {meta.kind.value}，不是 {expected_kind.value}"
            )

        payload = self._content.read(valid_id)
        actual = compute_checksum(payload)
        if actual != meta.checksum:
            logger.error(
                "Lab artifact checksum mismatch",
                artifact_id=valid_id,
                expected=meta.checksum,
                actual=actual,
            )
            raise ArtifactChecksumError(
                f"产物 {valid_id} 内容哈希不匹配（期望 {meta.checksum}，实际 {actual}），拒绝加载"
            )
        return deserialize(payload)

    # ── 查询 / 删除 ───────────────────────────────────────────────

    async def get(self, artifact_id: str) -> ArtifactMeta | None:
        """只取元数据，不碰内容文件。"""
        return await self._meta.get(validate_artifact_id(artifact_id))

    async def list(
        self,
        kind: ArtifactKind | None = None,
        name_contains: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[list[ArtifactMeta], int]:
        """按类别 / 名称模糊筛选，按创建时间倒序返回 `(当页记录, 总数)`。"""
        filters = ArtifactFilter(kind=kind, name_contains=name_contains)
        return await self._meta.list(filters, limit=limit, offset=offset)

    async def delete(self, artifact_id: str) -> bool:
        """
        删除产物（元数据 + 内容文件）。

        只要**任一侧**确实删掉了东西就返回 True：元数据丢了但内容文件还在
        （上一次写入半途失败留下的孤儿）也算一次真实的清理，
        此时返回 False 会让调用方收到 404，却又确实删了一个文件 —— 前后矛盾。
        """
        valid_id = validate_artifact_id(artifact_id)
        removed = await self._meta.delete(valid_id)
        content_removed = self._content.delete(valid_id)
        if removed or content_removed:
            logger.info(
                "Deleted lab artifact",
                artifact_id=valid_id,
                meta_removed=removed,
                content_removed=content_removed,
            )
        return removed or content_removed
