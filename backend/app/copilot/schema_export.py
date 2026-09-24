"""Pydantic 模型 → 工具参数 JSON Schema（V3 Wave B-c / I1）

**为什么不手写 JSON Schema**：手写的那份会与端点的真实校验漂移，模型照它生成的
参数会在真正调用时 422。这里统一从既有 Pydantic 模型导出，端点改字段 = 工具声明
自动跟着改。

**为什么要展开 `$ref`**：Pydantic 会把枚举 / 嵌套模型抽成 `$defs` + `$ref`。
云端大模型能处理，但本地小模型（Ollama 上的 7B/14B）对 `$ref` 的支持很不稳定 ——
经常直接把 `"$ref"` 当成字段名吐回来。展开成自包含的 schema 后所有模型一视同仁。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

#: 展开 `$ref` 时的递归深度上限 —— 自引用模型不会把这里转成死循环
_MAX_INLINE_DEPTH = 12


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """把 Pydantic 模型导出成自包含（无 `$ref`/`$defs`）的 JSON Schema。"""
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})
    inlined = _inline(raw, defs, depth=0)
    if isinstance(inlined, dict):
        inlined.pop("$defs", None)
        # title 对模型没有帮助，只是白白占 token
        inlined.pop("title", None)
    return inlined if isinstance(inlined, dict) else {}


def _inline(node: Any, defs: dict[str, Any], *, depth: int) -> Any:
    """递归把 `$ref` 替换成 `$defs` 里的定义本体。"""
    if depth > _MAX_INLINE_DEPTH:
        # 深到这一层多半是自引用模型，保留原样比无限展开安全
        return node
    if isinstance(node, list):
        return [_inline(item, defs, depth=depth + 1) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str):
        target = _resolve(ref, defs)
        if target is not None:
            merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
            return _inline(merged, defs, depth=depth + 1)

    return {
        key: _inline(value, defs, depth=depth + 1)
        for key, value in node.items()
        if key != "$defs"
    }


def _resolve(ref: str, defs: dict[str, Any]) -> dict[str, Any] | None:
    """只解析本地 `#/$defs/Name` 引用；外部引用一律放弃（不该出现）。"""
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        return None
    target = defs.get(ref[len(prefix):])
    return target if isinstance(target, dict) else None
