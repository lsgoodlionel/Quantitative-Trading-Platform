"""
投研产物库 —— 类型、协议与序列化基元（V4 · M4）

产物分三类：数据集（DATASET）/ 模型（MODEL）/ 信号（SIGNAL）。
**元数据落库（TimescaleDB），内容落文件系统** —— 模型二进制不进数据库。

⚠️ 安全边界（必须先读完再改这个模块）
------------------------------------------------------------------
产物内容用 `pickle` 序列化，反序列化 pickle 等于**执行任意代码**。
因此本模块只允许加载「本服务自己写入」的产物，具体由三道约束保证：

  1. **路径不接受用户输入**：`artifact_id` 由服务端生成 UUID4，
     写入/读取前都要过 `validate_artifact_id()`；用户提供的 `name`
     只是一个可读标签，绝不参与路径拼接（哪怕它含 `../`）。
  2. **加载前校验 checksum**：内容哈希与元数据不一致直接抛
     `ArtifactChecksumError` 并拒绝反序列化 —— 文件被篡改就打不开。
  3. **不得加载外部来源的产物**。任何「导入外部模型文件」的需求都必须
     先把序列化格式换成非 pickle（如 ONNX / safetensors）再谈，
     不能靠在本模块上打补丁绕过。

容量策略
------------------------------------------------------------------
**不限容量，只支持手动删除**（`CAPACITY_POLICY`）。刻意不做
`app/quant/experiments/recorder.py` 那种 `MAX_RECORDS` 滚动淘汰：
产物库存在的理由就是「调完参关掉浏览器，下次不用从头再来」，
一个会悄悄丢数据的介质等于没做。此策略经 API 暴露给前端明示。
"""

from __future__ import annotations

import hashlib
import pickle  # noqa: S403 — 仅用于本服务自产自用的产物，见模块 docstring
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

# 容量策略标识：无上限、仅手动删除。API 层原样透出，供前端明示给用户。
CAPACITY_POLICY = "unlimited-manual-delete"

# 内容文件后缀（pickle 序列化）
CONTENT_SUFFIX = ".pkl"

# pickle 协议：5 支持 out-of-band buffer，对大 DataFrame 更省内存
PICKLE_PROTOCOL = 5


class ArtifactKind(str, Enum):
    """产物类别。"""

    DATASET = "dataset"
    MODEL = "model"
    SIGNAL = "signal"


@dataclass(frozen=True)
class ArtifactMeta:
    """产物元数据（不可变）。内容本身不在这里，按 `artifact_id` 去文件系统取。"""

    artifact_id: str
    kind: ArtifactKind
    name: str
    created_at: datetime
    size_bytes: int
    #: 自由标注（策略名、因子公式、区间…），值统一转成字符串
    tags: dict[str, str] = field(default_factory=dict)
    #: 内容 sha256，用于加载前校验与去重
    checksum: str = ""

    def to_dict(self) -> dict[str, Any]:
        """API 友好的字典投影（枚举转字符串、时间转 ISO）。"""
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind.value,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "size_bytes": self.size_bytes,
            "tags": dict(self.tags),
            "checksum": self.checksum,
        }


@dataclass(frozen=True)
class ArtifactFilter:
    """列表筛选条件（全部可选）。"""

    kind: ArtifactKind | None = None
    name_contains: str | None = None


# ── 异常 ──────────────────────────────────────────────────────────

class LabError(Exception):
    """产物库错误基类。"""


class ArtifactNotFoundError(LabError):
    """产物不存在（元数据或内容文件缺失）。"""


class ArtifactChecksumError(LabError):
    """内容哈希与元数据不一致 —— 文件被篡改或写入损坏，拒绝加载。"""


class ArtifactKindMismatchError(LabError):
    """按错误的类别加载产物（如用 load_model 读一个 dataset）。"""


class InvalidArtifactIdError(LabError):
    """`artifact_id` 不是服务端生成的 UUID —— 拒绝用它拼路径。"""


# ── 标识与校验 ────────────────────────────────────────────────────

def new_artifact_id() -> str:
    """生成服务端产物 ID。**唯一**的 ID 来源，绝不用用户输入。"""
    return str(uuid.uuid4())


def validate_artifact_id(artifact_id: str) -> str:
    """
    校验并归一化 `artifact_id`，非 UUID 一律拒绝。

    这是路径穿越的唯一防线：只要 ID 必须是 UUID，
    `../../etc/passwd` 之类的输入就永远拼不进文件路径。
    """
    try:
        return str(uuid.UUID(str(artifact_id)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidArtifactIdError(f"非法产物 ID: {artifact_id!r}") from exc


def compute_checksum(payload: bytes) -> str:
    """内容 sha256 十六进制摘要。"""
    return hashlib.sha256(payload).hexdigest()


def normalize_tags(tags: dict[str, Any] | None) -> dict[str, str]:
    """标签统一为 str -> str，避免落库时出现无法 JSON 化的值。"""
    if not tags:
        return {}
    return {str(k): str(v) for k, v in tags.items()}


# ── 序列化 ────────────────────────────────────────────────────────

def serialize(obj: Any) -> bytes:
    """序列化产物内容。失败时抛 `LabError` 而非让 pickle 的异常裸奔。"""
    try:
        return pickle.dumps(obj, protocol=PICKLE_PROTOCOL)
    except (pickle.PicklingError, AttributeError, TypeError, RecursionError) as exc:
        raise LabError(f"产物内容无法序列化: {exc}") from exc


def deserialize(payload: bytes) -> Any:
    """
    反序列化产物内容。

    ⚠️ 调用方**必须**先校验 checksum（见 `LabStore._load`）。
    本函数不做任何校验，直接调用它等于把 pickle 的 RCE 面暴露出来。
    """
    try:
        return pickle.loads(payload)  # noqa: S301 — 见模块 docstring 的三道约束
    except Exception as exc:  # pickle 反序列化可能抛出任意异常类型
        raise LabError(f"产物内容无法反序列化: {exc}") from exc


# ── 仓储协议 ──────────────────────────────────────────────────────

class ArtifactMetaStore(Protocol):
    """产物元数据仓储接口（便于测试替换为内存实现）。"""

    async def save(self, meta: ArtifactMeta) -> ArtifactMeta: ...

    async def get(self, artifact_id: str) -> ArtifactMeta | None: ...

    async def list(
        self, filters: ArtifactFilter, limit: int, offset: int
    ) -> tuple[list[ArtifactMeta], int]: ...

    async def delete(self, artifact_id: str) -> bool: ...


class ArtifactContentStore(Protocol):
    """产物内容仓储接口（默认实现为本地文件系统）。"""

    def write(self, artifact_id: str, payload: bytes) -> int: ...

    def read(self, artifact_id: str) -> bytes: ...

    def delete(self, artifact_id: str) -> bool: ...

    def exists(self, artifact_id: str) -> bool: ...
