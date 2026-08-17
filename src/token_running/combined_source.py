"""合并数据源：同时聚合 DSH 轨迹 + Claude JSONL + cc-switch DB（codex/opencode）。

各子源接口一致（poll / daily_total / total / online），本类求和合并：
- poll() 合并所有启用于源的新事件（按秒分桶，同秒求和）
- daily_total / total 为所有启用于源之和
- online 任一启用于源在线即在线

**渠道过滤**：每个子源带名字（如 dsh / claude / codex），可通过 set_channel_enabled
控制显示哪些渠道（多选），用于浮窗菜单「只显示 codex / Claude Code / DSH」。

**避免重复**：cc-switch DB 的 claude 记录与 Claude JSONL 同源（JSONL → cc-switch 同步），
因此合并时 DB 子源应通过 TokenSource(app_types=["codex", "opencode"]) 排除 claude。
"""
from __future__ import annotations

import time
from typing import Callable, Sequence

from token_running.source import UsageEvent


class CombinedSource:
    """聚合多个同接口数据源的合并源，支持按渠道名过滤显示。"""

    def __init__(self, sources: Sequence, names: Sequence[str] | None = None,
                 now_fn: Callable[[], float] = time.time) -> None:
        self._sources = list(sources)
        if names is None:
            names = [type(s).__name__ for s in self._sources]
        self._names = list(names)
        self._now = now_fn
        self._enabled = set(range(len(self._sources)))  # 默认全选

    def channel_names(self) -> list[str]:
        return list(self._names)

    def is_channel_enabled(self, name: str) -> bool:
        try:
            return self._names.index(name) in self._enabled
        except ValueError:
            return False

    def set_channel_enabled(self, name: str, enabled: bool) -> None:
        try:
            idx = self._names.index(name)
        except ValueError:
            return
        if enabled:
            self._enabled.add(idx)
        else:
            self._enabled.discard(idx)

    def enabled_channels(self) -> list[str]:
        return [self._names[i] for i in sorted(self._enabled)]

    def _active(self) -> list:
        return [self._sources[i] for i in sorted(self._enabled)]

    def set_windows(self, today_since: int | None = None, total_since: int | None = None) -> None:
        """透传统计口径窗口起点到所有子源（仅对支持 set_windows 的源生效）。"""
        for s in self._sources:
            if hasattr(s, "set_windows"):
                s.set_windows(today_since, total_since)

    def online(self) -> bool:
        return any(s.online() for s in self._active())

    def poll(self) -> list[UsageEvent]:
        bucketed: dict[int, int] = {}
        for s in self._active():
            for ev in s.poll():
                bucketed[ev.ts] = bucketed.get(ev.ts, 0) + ev.tokens
        return [UsageEvent(ts, tokens) for ts, tokens in sorted(bucketed.items())]

    def daily_total(self) -> int:
        return sum(s.daily_total() for s in self._active())

    def total(self) -> int:
        return sum(s.total() for s in self._active())
