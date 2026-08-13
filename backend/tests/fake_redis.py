"""内存版 Redis 替身（测试用）

只实现本项目实际用到的命令：字符串 / 集合 / 有序集合 / pipeline。
不引入 fakeredis 依赖 —— 需求就这么点，一个依赖不值得。

命令语义刻意贴近 redis-py 的异步客户端：读写命令是协程，
`pipeline()` 返回的对象上命令是**同步**的（只入队），由 `execute()` 统一提交。
"""

from __future__ import annotations


class FakePipeline:
    """把命令排队，`execute()` 时按序执行。"""

    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._queued: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, command: str):
        def enqueue(*args, **kwargs) -> FakePipeline:
            self._queued.append((command, args, kwargs))
            return self

        return enqueue

    async def execute(self) -> list:
        results = [
            await getattr(self._client, command)(*args, **kwargs)
            for command, args, kwargs in self._queued
        ]
        self._queued.clear()
        return results


class FakeRedis:
    """够用的内存 Redis。"""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    # ── 字符串 ───────────────────────────────────────────────

    async def set(self, key: str, value: str) -> bool:
        self.strings[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.strings.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.strings.get(k) for k in keys]

    async def delete(self, key: str) -> int:
        existed = key in self.strings
        self.strings.pop(key, None)
        return int(existed)

    async def exists(self, key: str) -> int:
        return int(key in self.strings)

    # ── 集合 ─────────────────────────────────────────────────

    async def sadd(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        added = [m for m in members if m not in bucket]
        bucket.update(members)
        return len(added)

    async def srem(self, key: str, *members: str) -> int:
        bucket = self.sets.get(key, set())
        removed = [m for m in members if m in bucket]
        bucket.difference_update(members)
        return len(removed)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    # ── 有序集合 ─────────────────────────────────────────────

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        bucket = self.zsets.setdefault(key, {})
        added = [m for m in mapping if m not in bucket]
        bucket.update(mapping)
        return len(added)

    async def zrem(self, key: str, *members: str) -> int:
        bucket = self.zsets.get(key, {})
        return len([m for m in members if bucket.pop(m, None) is not None])

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        return self._sliced(key, start, end, reverse=False)

    async def zrevrange(self, key: str, start: int, end: int) -> list[str]:
        return self._sliced(key, start, end, reverse=True)

    def _sliced(self, key: str, start: int, end: int, *, reverse: bool) -> list[str]:
        ordered = sorted(self.zsets.get(key, {}).items(), key=lambda kv: (kv[1], kv[0]))
        if reverse:
            ordered.reverse()
        members = [member for member, _ in ordered]
        # Redis 的 end 是闭区间，-1 表示到末尾
        stop = len(members) if end == -1 else end + 1
        return members[start:stop]
