"""应用版本号的唯一来源。

此前 `main.py` 与 `api/v1/router.py` 各硬编码了一份 `"0.1.0"`，发版时必然漏改一处，
于是两个健康检查端点会报出不同的版本 —— 排查线上问题时这是最容易误导人的一类信息。

`pyproject.toml` 的 `version` 与此处保持一致，由 `tests/test_health.py` 断言看守。
不直接用 `importlib.metadata.version()` 读取：本项目以源码目录方式运行（容器里没有
`pip install .`），该调用在运行时会抛 `PackageNotFoundError`。
"""

APP_VERSION = "0.1.0"
