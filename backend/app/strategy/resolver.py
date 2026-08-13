"""
用户策略加载器（Wave O-a / O2）

把 `settings.user_strategies_dir` 下的 `.py` 文件加载进来，与 16 个 preset 合并成
一张「可用策略」表，让用户不必改源码重启就能跑自己的策略。

⚠️ **安全边界（请完整读完再启用）**

加载该目录下的文件**等同于以服务进程的权限执行它**。本模块**没有沙箱**，也不打算
假装有：`import os; os.system(...)` 可以藏在任意表达式里，AST 白名单也能被
`getattr(__builtins__, ...)` 之类的写法绕过。因此：

1. **只把你自己写的、或已经审阅过的策略文件放进该目录。**
2. **默认关闭**（`settings.user_strategies_enabled=False`），启用是部署方的显式决定。
3. 目录路径**只从 settings 读，不接受任何请求参数** —— 否则就多了一个任意路径读取面。
4. 本期**不提供** HTTP 上传端点：一个能上传即执行的端点等于远程代码执行漏洞。
   把文件放进目录即可，该目录的写权限由部署方自己控制。

**关于「热」的程度**：合并视图有缓存。新增/修改文件后需要显式调用
`reload_user_strategies()`（CLI 每次都是新进程，天然是最新的）或重启服务。
不每次调用都重扫，是因为重扫意味着**重新执行**用户模块的顶层代码 —— 那既慢又有
副作用（用户模块里的连接、定时器会被反复创建）。

`STRATEGY_REGISTRY` 本身**保持不变**，preset 的加载路径完全不受本模块影响。

**重名会炸，这是有意的**：`available_strategies()` 遇到重名直接抛
`StrategyNameConflictError`，端点侧目前表现为 500。宁可让一个已启用该功能的用户
看到一次刺眼的报错，也不要让他在「我明明跑的是 macd」的困惑里查一整天。
（更友好的 4xx 需要在 `app/main.py` 注册异常处理器，那是共享文件，留待主线合并。）
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from app.strategy.base import StrategyBase
from app.strategy.presets import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

__all__ = [
    "DiscoveryResult",
    "LoadError",
    "StrategyNameConflictError",
    "available_strategies",
    "discover_strategies",
    "reload_user_strategies",
    "reset_strategy_cache",
    "user_strategy_errors",
]

#: 加载出来的用户模块挂在这个包名下，避免与真实模块重名把 `sys.modules` 弄脏
_MODULE_PREFIX = "app.strategy._user"

#: 基类自带的 name 默认值。策略类没覆盖它就说明作者忘了取名，不替他猜
_UNNAMED = {StrategyBase.name, "unnamed_portfolio_strategy"}


class StrategyNameConflictError(RuntimeError):
    """用户策略与 preset（或另一个用户策略）重名。

    刻意做成硬错误而不是「后来者覆盖」：静默覆盖会让「我明明跑的是 macd」
    变成一个查不出来的谜 —— 回测结果对不上，而代码里没有任何痕迹。
    """

    def __init__(self, conflicts: tuple[str, ...] | list[str]) -> None:
        # 传字符串会被当成字符序列，join 出「策, 略, 名, ...」这种看起来发疯的消息。
        # 签名已标注 tuple，但 Python 不强制，而这个失败模式的排查成本远高于一行守卫。
        if isinstance(conflicts, str):
            conflicts = (conflicts,)
        self.conflicts = tuple(conflicts)
        super().__init__(
            f"用户策略与已有策略重名：{', '.join(self.conflicts)}。"
            "请改掉用户策略里的 name（用户策略不会覆盖 preset）。"
        )


@dataclass(frozen=True)
class LoadError:
    """单个文件的加载失败记录。"""

    path: str
    reason: str


@dataclass(frozen=True)
class DiscoveryResult:
    """扫描结果。

    `errors` 是返回体的一部分而不只是日志 —— 用户需要知道「我那个文件为什么没出现」，
    翻服务日志不是一个合理的答案。
    """

    strategies: dict[str, type[StrategyBase]]
    errors: tuple[LoadError, ...] = ()


def discover_strategies(root: Path) -> DiscoveryResult:
    """
    扫描 `root` 下的 `.py`，加载其中 `StrategyBase` 的具体子类。

    - 同名（与 preset 或彼此）→ 抛 `StrategyNameConflictError` 并列出冲突名
    - 单个文件加载失败 → 记进 `errors` 并跳过，不影响其他文件
    - 目录不存在 → 空结果，不是错误（用户还没建目录而已）

    ⚠️ 本函数会**执行**这些文件，见模块 docstring 的安全边界。
    """
    if not root.is_dir():
        return DiscoveryResult(strategies={})

    strategies: dict[str, type[StrategyBase]] = {}
    errors: list[LoadError] = []
    conflicts: list[str] = []

    for path in sorted(root.glob("*.py")):
        if path.name.startswith(("_", ".")):
            continue
        try:
            module = _load_module(path)
        except Exception as exc:  # noqa: BLE001 — 用户代码什么都可能抛
            logger.warning("用户策略文件加载失败，已跳过 · %s: %s", path, exc)
            errors.append(LoadError(path=str(path), reason=f"{type(exc).__name__}: {exc}"))
            continue
        _collect_from_module(module, path, strategies, errors, conflicts)

    if conflicts:
        raise StrategyNameConflictError(tuple(sorted(set(conflicts))))
    return DiscoveryResult(strategies=strategies, errors=tuple(errors))


def _collect_from_module(
    module: object,
    path: Path,
    strategies: dict[str, type[StrategyBase]],
    errors: list[LoadError],
    conflicts: list[str],
) -> None:
    """把一个已加载模块里的策略类收进 `strategies`，冲突/无名记到对应列表。"""
    for cls in _strategy_classes(module):
        name = getattr(cls, "name", "")
        if not name or name in _UNNAMED:
            errors.append(LoadError(
                path=str(path),
                reason=f"策略类 {cls.__name__} 未设置 name，无法确定策略键名",
            ))
            continue
        if name in STRATEGY_REGISTRY or name in strategies:
            conflicts.append(name)
            continue
        strategies[name] = cls


def _strategy_classes(module: object) -> list[type[StrategyBase]]:
    """取模块里**自己定义**的具体策略类（import 进来的基类/别的策略不算）。"""
    module_name = getattr(module, "__name__", "")
    found: list[type[StrategyBase]] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if not issubclass(obj, StrategyBase) or obj is StrategyBase:
            continue
        if obj.__module__ != module_name or inspect.isabstract(obj):
            continue
        found.append(obj)
    return found


def _load_module(path: Path):
    """按文件路径加载模块。失败一律上抛，由调用方记进 errors。"""
    module_name = f"{_MODULE_PREFIX}.{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {path} 构造模块 spec")

    module = importlib.util.module_from_spec(spec)
    # 先进 sys.modules 再 exec：dataclass / 类型注解求值会回查自身模块
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


# ── 合并视图（带缓存） ────────────────────────────────────────

_cache: DiscoveryResult | None = None


def reset_strategy_cache() -> None:
    """丢弃缓存，下次访问合并视图时重新扫描。测试与 `reload` 都用它。"""
    global _cache
    _cache = None


def reload_user_strategies() -> DiscoveryResult:
    """显式重扫用户策略目录（会重新执行这些文件），返回本次扫描结果。"""
    reset_strategy_cache()
    return _discovery()


def user_strategy_errors() -> tuple[LoadError, ...]:
    """当前缓存里记录的加载失败明细，供 CLI/端点回显给用户。"""
    return _discovery().errors


def available_strategies() -> dict[str, type[StrategyBase]]:
    """
    preset + 用户策略的合并视图（每次返回新 dict，调用方随便改）。

    `settings.user_strategies_enabled=False`（默认）时逐键等于 `STRATEGY_REGISTRY`
    —— preset 的加载路径完全不受本模块影响。
    """
    merged: dict[str, type[StrategyBase]] = dict(STRATEGY_REGISTRY)
    merged.update(_discovery().strategies)
    return merged


def _discovery() -> DiscoveryResult:
    """取（必要时先建）当前的扫描结果缓存。"""
    global _cache
    from app.core.config import settings

    if not settings.user_strategies_enabled:
        return DiscoveryResult(strategies={})
    if _cache is None:
        _cache = discover_strategies(Path(settings.user_strategies_dir))
    return _cache
