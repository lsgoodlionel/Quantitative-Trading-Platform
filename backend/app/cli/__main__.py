"""`python -m app.cli` 的入口。真正的逻辑在 `app.cli.main`。"""

from __future__ import annotations

from app.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
