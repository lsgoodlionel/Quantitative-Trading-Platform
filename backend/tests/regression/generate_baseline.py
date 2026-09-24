"""重新生成 golden 基线。

用法（仅在**有意**变更引擎行为时执行，并须在 PR 说明差异原因）：

    cd backend && .venv/bin/python -m tests.regression.generate_baseline

生成后务必 `git diff` 审阅：非预期的用例差异说明改动引入了行为漂移。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.regression.fixtures import case_id, iter_cases
from tests.regression.snapshot import build_snapshot

GOLDEN_PATH = Path(__file__).parent / "golden" / "engine_baseline.json"


def generate() -> dict:
    cases = iter_cases()
    snapshots: dict[str, dict] = {}
    for i, (name, market, regime) in enumerate(cases, 1):
        cid = case_id(name, market, regime)
        try:
            snapshots[cid] = build_snapshot(name, market, regime)
        except Exception as exc:  # 基线必须完整：任何用例失败都要显式暴露
            print(f"[{i}/{len(cases)}] FAILED {cid}: {exc}", file=sys.stderr)
            raise
        print(f"[{i}/{len(cases)}] {cid} "
              f"fills={snapshots[cid]['fills_count']} "
              f"final={snapshots[cid]['final_value']}")
    return {"version": 1, "cases": snapshots}


def main() -> None:
    payload = generate()
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    size_kb = GOLDEN_PATH.stat().st_size / 1024
    print(f"\nwrote {GOLDEN_PATH} — {len(payload['cases'])} cases, {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
