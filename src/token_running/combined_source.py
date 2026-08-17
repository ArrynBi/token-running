"""合并数据源：同时聚合 DSH 轨迹 + Claude JSONL + cc-switch DB（codex/opencode）。

各子源接口一致（poll / daily_total / total / online），本类求和合并：
- poll() 合并所有子源的新事件（按秒分桶，同秒求和）
- daily_total / total 为所有子源之和
- online 任一子源在线即在线

**避免重复**：cc-switch DB 的 claude 记录与 Claude JSONL 同源（JSONL → cc-switch 同步），
因此合并时 DB 子源应通过 TokenSource(app_types=["codex", "opencode"]) 排除 claude。
"""
from __future__ import annotations

import time
from typing import Callable, Sequence

from token_running.source import UsageEvent


class CombinedSource:
    """聚合多个同接口数据源的合并源。"""

    def __init__(self, sources: Sequence, now_fn: Callable[[], float] = time.time) -> None:
        self._sources = list(sources)
        self._now = now_fn

    def online(self) -> bool:
        return any(s.online() for s in self._sources)

    def poll(self) -> list[UsageEvent]:
        bucketed: dict[int, int] = {}
        for s in self._sources:
            for ev in s.poll():
                bucketed[ev.ts] = bucketed.get(ev.ts, 0) + ev.tokens
        return [UsageEvent(ts, tokens) for ts, tokens in sorted(bucketed.items())]

    def daily_total(self) -> int:
        return sum(s.daily_total() for s in self._sources)

    def total(self) -> int:
        return sum(s.total() for s in self._sources)
