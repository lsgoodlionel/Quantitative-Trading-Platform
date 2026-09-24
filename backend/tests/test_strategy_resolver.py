"""
用户策略加载器测试（Wave O-a / O2）

覆盖契约四条硬要求：
1. 发现并加载合法的用户策略
2. 与 preset 同名 → 报错并列出冲突名，不静默覆盖
3. 单个文件语法错误 → 跳过该文件，其余照常加载，errors 里有记录
4. user_strategies_enabled=False 时 available_strategies() 与 STRATEGY_REGISTRY 逐键相等

⚠️ 本模块**不测沙箱** —— 因为根本没有沙箱。加载用户文件等同于执行它，
见 `app/strategy/resolver.py` 的模块 docstring。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.strategy.base import StrategyBase
from app.strategy.presets import STRATEGY_REGISTRY
from app.strategy.resolver import (
    StrategyNameConflictError,
    available_strategies,
    discover_strategies,
    reload_user_strategies,
    user_strategy_errors,
)

# ── 测试用策略源码 ────────────────────────────────────────────

_VALID_STRATEGY = textwrap.dedent('''\
    """合法的用户策略。"""
    from __future__ import annotations

    from app.strategy.base import StrategyBase


    class MyEdgeStrategy(StrategyBase):
        name = "my_edge"
        description = "测试用用户策略"

        def on_bar(self, ctx) -> None:
            pass
''')

_SECOND_STRATEGY = textwrap.dedent('''\
    from app.strategy.base import StrategyBase


    class OtherStrategy(StrategyBase):
        name = "my_other"

        def on_bar(self, ctx) -> None:
            pass
''')

_SYNTAX_ERROR = "class Broken(StrategyBase:\n    pass\n"

_PRESET_COLLISION = textwrap.dedent('''\
    from app.strategy.base import StrategyBase


    class FakeMacd(StrategyBase):
        name = "macd"

        def on_bar(self, ctx) -> None:
            pass
''')

_NO_NAME = textwrap.dedent('''\
    from app.strategy.base import StrategyBase


    class NamelessStrategy(StrategyBase):
        def on_bar(self, ctx) -> None:
            pass
''')

_NO_STRATEGY = "VALUE = 42\n"


def _write(root: Path, filename: str, source: str) -> Path:
    path = root / filename
    path.write_text(source, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前后都清缓存，避免合并视图跨用例串味。"""
    from app.strategy import resolver

    resolver.reset_strategy_cache()
    yield
    resolver.reset_strategy_cache()


# ── 1. 发现并加载合法的用户策略 ───────────────────────────────

def test_discovers_valid_user_strategy(tmp_path: Path) -> None:
    # Arrange
    _write(tmp_path, "my_edge.py", _VALID_STRATEGY)

    # Act
    result = discover_strategies(tmp_path)

    # Assert
    assert set(result.strategies) == {"my_edge"}
    assert issubclass(result.strategies["my_edge"], StrategyBase)
    assert result.errors == ()


def test_discovers_multiple_files(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", _VALID_STRATEGY)
    _write(tmp_path, "b.py", _SECOND_STRATEGY)

    result = discover_strategies(tmp_path)

    assert set(result.strategies) == {"my_edge", "my_other"}


def test_missing_directory_yields_empty_result(tmp_path: Path) -> None:
    """目录不存在不是错误 —— 用户还没建目录而已，不该炸。"""
    result = discover_strategies(tmp_path / "nope")

    assert result.strategies == {}
    assert result.errors == ()


def test_skips_underscore_and_dot_prefixed_files(tmp_path: Path) -> None:
    """`__init__.py` / `_helpers.py` / 编辑器临时文件不当成策略文件。"""
    _write(tmp_path, "__init__.py", _VALID_STRATEGY)
    _write(tmp_path, "_helper.py", _VALID_STRATEGY)
    _write(tmp_path, ".hidden.py", _VALID_STRATEGY)

    result = discover_strategies(tmp_path)

    assert result.strategies == {}
    assert result.errors == ()


def test_file_without_strategy_class_is_not_an_error(tmp_path: Path) -> None:
    """纯工具模块放在同目录很常见，不该报错。"""
    _write(tmp_path, "utils.py", _NO_STRATEGY)

    result = discover_strategies(tmp_path)

    assert result.strategies == {}
    assert result.errors == ()


def test_strategy_without_explicit_name_is_reported(tmp_path: Path) -> None:
    """没显式设 name 的策略被跳过并记 error，而不是猜一个键出来。"""
    _write(tmp_path, "nameless.py", _NO_NAME)

    result = discover_strategies(tmp_path)

    assert result.strategies == {}
    assert len(result.errors) == 1
    assert "NamelessStrategy" in result.errors[0].reason
    assert "name" in result.errors[0].reason


# ── 2. 与 preset 同名 → 报错并列出冲突名 ─────────────────────

def test_preset_name_collision_raises_with_conflict_names(tmp_path: Path) -> None:
    """静默覆盖会让「我明明跑的是 macd」变成一个谜，所以这里必须炸。"""
    _write(tmp_path, "fake_macd.py", _PRESET_COLLISION)

    with pytest.raises(StrategyNameConflictError) as exc:
        discover_strategies(tmp_path)

    assert "macd" in str(exc.value)
    assert exc.value.conflicts == ("macd",)


def test_preset_class_is_not_overridden_by_user_file(tmp_path: Path, monkeypatch) -> None:
    """冲突时 preset 必须原封不动 —— 合并视图整体失败，而不是被替换。"""
    _write(tmp_path, "fake_macd.py", _PRESET_COLLISION)
    _enable_user_strategies(monkeypatch, tmp_path)

    with pytest.raises(StrategyNameConflictError):
        available_strategies()

    assert STRATEGY_REGISTRY["macd"].__module__.startswith("app.strategy.presets")


def test_duplicate_names_between_user_files_raise(tmp_path: Path) -> None:
    """两个用户文件抢同一个名字，同样不允许后来者静默覆盖。"""
    _write(tmp_path, "a.py", _VALID_STRATEGY)
    _write(tmp_path, "b.py", _VALID_STRATEGY.replace("MyEdgeStrategy", "MyEdgeClone"))

    with pytest.raises(StrategyNameConflictError) as exc:
        discover_strategies(tmp_path)

    assert exc.value.conflicts == ("my_edge",)


# ── 3. 单文件失败 → 跳过并记录，其余照常 ─────────────────────

def test_syntax_error_file_is_skipped_and_recorded(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", _SYNTAX_ERROR)
    _write(tmp_path, "good.py", _VALID_STRATEGY)

    result = discover_strategies(tmp_path)

    assert set(result.strategies) == {"my_edge"}
    assert len(result.errors) == 1
    assert result.errors[0].path.endswith("broken.py")
    assert result.errors[0].reason  # 原因可查，不只是日志


def test_import_error_file_is_skipped_and_recorded(tmp_path: Path) -> None:
    """运行期 import 失败（缺依赖）同样只废掉该文件。"""
    _write(tmp_path, "bad_import.py", "import definitely_not_a_real_module_xyz\n")
    _write(tmp_path, "good.py", _VALID_STRATEGY)

    result = discover_strategies(tmp_path)

    assert set(result.strategies) == {"my_edge"}
    assert len(result.errors) == 1
    assert "definitely_not_a_real_module_xyz" in result.errors[0].reason


def test_errors_are_queryable_from_merged_view(tmp_path: Path, monkeypatch) -> None:
    """加载失败的原因要能查 —— 合并视图之外另有一个只读入口。"""
    _write(tmp_path, "broken.py", _SYNTAX_ERROR)
    _enable_user_strategies(monkeypatch, tmp_path)

    available_strategies()

    assert len(user_strategy_errors()) == 1


# ── 4. 默认关闭时合并视图 == STRATEGY_REGISTRY ────────────────

def test_disabled_by_default_in_settings() -> None:
    from app.core.config import settings

    assert settings.user_strategies_enabled is False


def test_available_strategies_equals_registry_when_disabled(tmp_path: Path, monkeypatch) -> None:
    """默认路径必须完全不受影响：逐键相等，且 preset 的类对象一模一样。"""
    from app.core.config import settings

    _write(tmp_path, "my_edge.py", _VALID_STRATEGY)
    monkeypatch.setattr(settings, "user_strategies_enabled", False)
    monkeypatch.setattr(settings, "user_strategies_dir", str(tmp_path))

    merged = available_strategies()

    assert merged == STRATEGY_REGISTRY
    assert list(merged) == list(STRATEGY_REGISTRY)
    for key, cls in STRATEGY_REGISTRY.items():
        assert merged[key] is cls


def test_available_strategies_merges_when_enabled(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, "my_edge.py", _VALID_STRATEGY)
    _enable_user_strategies(monkeypatch, tmp_path)

    merged = available_strategies()

    assert set(merged) == set(STRATEGY_REGISTRY) | {"my_edge"}
    for key, cls in STRATEGY_REGISTRY.items():
        assert merged[key] is cls


def test_merged_view_is_a_copy(tmp_path: Path, monkeypatch) -> None:
    """调用方改返回值不该污染注册表。"""
    _enable_user_strategies(monkeypatch, tmp_path)

    merged = available_strategies()
    merged.pop("macd", None)

    assert "macd" in STRATEGY_REGISTRY
    assert "macd" in available_strategies()


def test_reload_picks_up_newly_added_file(tmp_path: Path, monkeypatch) -> None:
    """热加载：新文件落盘后显式 reload 即可生效，无需改源码重启。"""
    _enable_user_strategies(monkeypatch, tmp_path)
    assert "my_edge" not in available_strategies()

    _write(tmp_path, "my_edge.py", _VALID_STRATEGY)
    reload_user_strategies()

    assert "my_edge" in available_strategies()


def test_available_strategies_is_cached(tmp_path: Path, monkeypatch) -> None:
    """不显式 reload 就不重复执行用户模块 —— 每次请求都 exec 用户代码不可接受。"""
    _enable_user_strategies(monkeypatch, tmp_path)
    available_strategies()

    _write(tmp_path, "my_edge.py", _VALID_STRATEGY)

    assert "my_edge" not in available_strategies()


# ── 辅助 ─────────────────────────────────────────────────────

def _enable_user_strategies(monkeypatch, root: Path) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "user_strategies_enabled", True)
    monkeypatch.setattr(settings, "user_strategies_dir", str(root))


# ── 同名冲突在 HTTP 层是 409 而不是 500 ────────────────────────────
#
# 500 的含义是「服务端出了意料之外的问题」。用户在策略目录里放了一个与 preset
# 重名的文件是**用户能自己修好的配置问题** —— 报 500 会让人以为平台坏了，
# 而真正的原因（哪两个名字撞了）还埋在服务端日志里。

def test_name_conflict_surfaces_as_409_not_500(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.main import _register_exception_handlers
    from app.strategy.resolver import StrategyNameConflictError

    app = FastAPI()

    @app.get("/boom")
    async def _boom():
        raise StrategyNameConflictError(("double_ma",))

    _register_exception_handlers(app)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/boom")

    assert resp.status_code == 409
    # 冲突的具体名字要回到用户手里，否则他不知道该改哪个文件
    assert "double_ma" in resp.json()["detail"]


def test_conflict_error_tolerates_a_bare_string() -> None:
    """传单个字符串不该被当成字符序列。

    `', '.join("abc")` 得到 "a, b, c" —— 消息会变成一串逗号分隔的汉字，
    看起来像程序疯了，排查成本远高于构造函数里那一行守卫。
    """
    from app.strategy.resolver import StrategyNameConflictError

    err = StrategyNameConflictError("double_ma")

    assert err.conflicts == ("double_ma",)
    assert "double_ma" in str(err)
    assert "d, o, u" not in str(err)
