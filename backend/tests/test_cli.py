"""
CLI 工具链测试（Wave O-a / O3）

覆盖契约三条硬要求：
1. list-strategies 在无 DB/Redis 环境下正常输出（不抛 traceback）
2. 参数非法 → 退出码 2；运行失败 → 退出码 1；成功 → 0
3. new-strategy 生成的骨架文件能被 discover_strategies 加载

CLI 是薄壳，业务逻辑都在服务层 —— 这里验的是「壳」的行为：退出码、错误措辞、
以及「没起 docker 也别甩一屏 traceback」。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.cli.errors import EXIT_OK, EXIT_RUNTIME_ERROR, EXIT_USAGE_ERROR
from app.cli.main import main
from app.strategy.resolver import discover_strategies

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_cache():
    from app.strategy import resolver

    resolver.reset_strategy_cache()
    yield
    resolver.reset_strategy_cache()


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch) -> Path:
    """启用用户策略并指向一个临时目录。"""
    from app.core.config import settings

    root = tmp_path / "strategies"
    root.mkdir()
    monkeypatch.setattr(settings, "user_strategies_enabled", True)
    monkeypatch.setattr(settings, "user_strategies_dir", str(root))
    return root


# ── 1. list-strategies：无 DB/Redis 也能跑 ────────────────────

def test_list_strategies_succeeds(capsys) -> None:
    code = main(["list-strategies"])

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "double_ma" in out
    assert "macd" in out


def test_list_strategies_json_output(capsys) -> None:
    code = main(["list-strategies", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert {"name", "description", "source"} <= set(payload[0])
    assert {item["source"] for item in payload} == {"preset"}


def test_list_strategies_without_db_or_redis_has_no_traceback() -> None:
    """真起一个子进程，DB/Redis 指向必然连不上的端口。"""
    env = {
        "PATH": "/usr/bin:/bin",
        "DATABASE_URL": "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/none",
        "REDIS_URL": "redis://127.0.0.1:1/0",
    }
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "list-strategies"],
        cwd=_BACKEND_ROOT, capture_output=True, text=True, timeout=180, env=env,
    )

    assert result.returncode == EXIT_OK, result.stderr
    assert "double_ma" in result.stdout
    assert "Traceback" not in result.stderr


def test_list_strategies_includes_user_strategies(user_dir: Path, capsys) -> None:
    (user_dir / "edge.py").write_text(_VALID_STRATEGY, encoding="utf-8")

    code = main(["list-strategies", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert {"name": "my_edge", "description": "测试用", "source": "user"} in payload


def test_list_strategies_reports_load_errors(user_dir: Path, capsys) -> None:
    """坏文件不该被静默吞掉 —— 用户要能看到「我那个文件为什么没出现」。"""
    (user_dir / "broken.py").write_text("class Broken(:\n", encoding="utf-8")

    code = main(["list-strategies"])

    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert "broken.py" in captured.err


# ── 2. 退出码 ─────────────────────────────────────────────────

def test_unknown_subcommand_exits_2() -> None:
    assert main(["definitely-not-a-command"]) == EXIT_USAGE_ERROR


def test_no_subcommand_exits_2(capsys) -> None:
    assert main([]) == EXIT_USAGE_ERROR


def test_missing_required_argument_exits_2() -> None:
    assert main(["backtest", "--strategy", "double_ma"]) == EXIT_USAGE_ERROR


def test_unknown_strategy_exits_2(capsys) -> None:
    code = main([
        "backtest", "--strategy", "no_such_strategy", "--symbol", "AAPL",
        "--start", "2024-01-01", "--end", "2024-03-01",
    ])

    assert code == EXIT_USAGE_ERROR
    assert "no_such_strategy" in capsys.readouterr().err


def test_bad_date_range_exits_2(capsys) -> None:
    code = main([
        "backtest", "--strategy", "double_ma", "--symbol", "AAPL",
        "--start", "2024-03-01", "--end", "2024-01-01",
    ])

    assert code == EXIT_USAGE_ERROR
    assert "早于" in capsys.readouterr().err


def test_bad_params_json_exits_2(capsys) -> None:
    code = main([
        "backtest", "--strategy", "double_ma", "--symbol", "AAPL",
        "--start", "2024-01-01", "--end", "2024-03-01", "--params", "{not json",
    ])

    assert code == EXIT_USAGE_ERROR
    assert "--params" in capsys.readouterr().err


def test_download_without_symbols_exits_2(capsys) -> None:
    assert main(["download", "--symbols", " , ", "--market", "US"]) == EXIT_USAGE_ERROR


def test_runtime_failure_exits_1(monkeypatch, capsys) -> None:
    """取数炸了 → 一句人话 + 退出码 1，不是一屏 traceback。"""
    async def _boom(*_args, **_kwargs):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr("app.data.service.DataService.get_bars", _boom)

    code = main([
        "backtest", "--strategy", "double_ma", "--symbol", "AAPL",
        "--start", "2024-01-01", "--end", "2024-03-01",
    ])

    captured = capsys.readouterr()
    assert code == EXIT_RUNTIME_ERROR
    assert "Traceback" not in captured.err
    assert captured.err.strip()


def test_strategy_name_conflict_exits_1(user_dir: Path, capsys) -> None:
    """用户策略与 preset 撞名：报错列出冲突名，而不是静默覆盖。"""
    (user_dir / "fake.py").write_text(
        _VALID_STRATEGY.replace("my_edge", "macd"), encoding="utf-8"
    )

    code = main(["list-strategies"])

    err = capsys.readouterr().err
    assert code == EXIT_RUNTIME_ERROR
    assert "macd" in err
    assert "Traceback" not in err


def test_backtest_success_exits_0(monkeypatch, capsys) -> None:
    from app.data.models import Bar

    bars = _fake_bars(120)

    async def _bars(*_args, **_kwargs) -> list[Bar]:
        return bars

    monkeypatch.setattr("app.data.service.DataService.get_bars", _bars)

    code = main([
        "backtest", "--strategy", "double_ma", "--symbol", "AAPL",
        "--start", "2024-01-01", "--end", "2024-06-01", "--json",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["strategy"] == "double_ma"
    assert payload["bars"] == 120
    assert "metrics" in payload


def test_download_delegates_to_archive_task(monkeypatch, capsys) -> None:
    """CLI 是薄壳：download 必须调既有归档任务，不复制业务逻辑。"""
    seen: dict = {}

    def _fake_apply(kwargs=None):
        seen.update(kwargs or {})
        return _EagerResult({
            "market": "US", "frequency": "1d", "requested": 2,
            "archived": 2, "written": 100, "errors": 0, "failed": [],
        })

    monkeypatch.setattr("app.tasks.archive.download_archive.apply", _fake_apply)

    code = main([
        "download", "--symbols", "AAPL,MSFT", "--market", "US",
        "--start", "2024-01-01", "--end", "2024-03-01",
    ])

    assert code == EXIT_OK
    assert seen["symbols"] == ["AAPL", "MSFT"]
    assert seen["market"] == "US"
    assert seen["start"] == "2024-01-01"
    assert "写入 100 根 bar" in capsys.readouterr().out


def test_download_partial_failure_exits_1(monkeypatch, capsys) -> None:
    def _fake_apply(kwargs=None):
        return _EagerResult({
            "market": "US", "frequency": "1d", "requested": 1,
            "archived": 0, "written": 0, "errors": 1, "failed": ["AAPL: 数据源全挂"],
        })

    monkeypatch.setattr("app.tasks.archive.download_archive.apply", _fake_apply)

    code = main(["download", "--symbols", "AAPL", "--market", "US"])

    assert code == EXIT_RUNTIME_ERROR
    assert "AAPL" in capsys.readouterr().err


# ── 3. new-strategy 产物能被 discover_strategies 加载 ─────────

def test_new_strategy_output_is_loadable(user_dir: Path, capsys) -> None:
    code = main(["new-strategy", "--name", "my_alpha"])

    assert code == EXIT_OK
    generated = user_dir / "my_alpha.py"
    assert generated.exists()
    assert str(generated) in capsys.readouterr().out

    result = discover_strategies(user_dir)
    assert set(result.strategies) == {"my_alpha"}
    assert result.errors == ()
    assert result.strategies["my_alpha"].__name__ == "MyAlphaStrategy"


def test_new_strategy_honours_explicit_dir(tmp_path: Path) -> None:
    target = tmp_path / "custom"

    code = main(["new-strategy", "--name", "my_alpha", "--dir", str(target)])

    assert code == EXIT_OK
    assert (target / "my_alpha.py").exists()


def test_new_strategy_rejects_invalid_name(user_dir: Path, capsys) -> None:
    assert main(["new-strategy", "--name", "Bad-Name"]) == EXIT_USAGE_ERROR
    assert "snake_case" in capsys.readouterr().err


def test_new_strategy_rejects_preset_name(user_dir: Path, capsys) -> None:
    """先挡住撞名，别等到加载时才炸。"""
    code = main(["new-strategy", "--name", "macd"])

    assert code == EXIT_USAGE_ERROR
    assert "macd" in capsys.readouterr().err


def test_new_strategy_refuses_to_overwrite(user_dir: Path, capsys) -> None:
    (user_dir / "my_alpha.py").write_text("# 我的心血\n", encoding="utf-8")

    code = main(["new-strategy", "--name", "my_alpha"])

    assert code == EXIT_RUNTIME_ERROR
    assert (user_dir / "my_alpha.py").read_text(encoding="utf-8") == "# 我的心血\n"
    assert "--force" in capsys.readouterr().err


def test_new_strategy_force_overwrites(user_dir: Path) -> None:
    (user_dir / "my_alpha.py").write_text("# 旧的\n", encoding="utf-8")

    code = main(["new-strategy", "--name", "my_alpha", "--force"])

    assert code == EXIT_OK
    assert "MyAlphaStrategy" in (user_dir / "my_alpha.py").read_text(encoding="utf-8")


# ── 辅助 ─────────────────────────────────────────────────────

_VALID_STRATEGY = textwrap.dedent('''\
    from app.strategy.base import StrategyBase


    class MyEdgeStrategy(StrategyBase):
        name = "my_edge"
        description = "测试用"

        def on_bar(self, ctx) -> None:
            pass
''')


class _EagerResult:
    """替身：只需要 `.result`。"""

    def __init__(self, result: dict) -> None:
        self.result = result


def _fake_bars(count: int) -> list:
    from datetime import UTC, datetime, timedelta

    from app.data.models import Bar, Frequency, Market

    base = datetime(2024, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(count):
        price = 100.0 + (i % 20) - (i % 7) * 1.5
        bars.append(Bar(
            symbol="AAPL", market=Market.US, frequency=Frequency.DAY_1,
            time=base + timedelta(days=i),
            open=price, high=price + 1, low=price - 1, close=price, volume=1_000_000,
        ))
    return bars
