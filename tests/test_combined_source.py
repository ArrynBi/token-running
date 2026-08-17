"""CombinedSource 测试：合并 DSH 轨迹 + Claude JSONL + cc-switch DB（codex/opencode）。

避免重复：DB 的 claude 记录与 JSONL 同源，合并时 DB 只统计 codex/opencode。
"""
import sqlite3
import time

from token_running.source import UsageEvent
from token_running.combined_source import CombinedSource


def _local_midnight(ts: float) -> int:
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


DAY_START = _local_midnight(1_700_000_000)


class FakeSrc:
    """简单假源：返回预设事件与累计值。"""
    def __init__(self, events=None, daily=0, total=0, online=True):
        self.events = events or []
        self._daily = daily
        self._total = total
        self._online = online
    def poll(self):
        e = self.events
        self.events = []
        return e
    def daily_total(self): return self._daily
    def total(self): return self._total
    def online(self): return self._online


def test_combines_poll_events(tmp_path):
    dsh = FakeSrc(events=[UsageEvent(DAY_START + 1, 100)])
    jsonl = FakeSrc(events=[UsageEvent(DAY_START + 2, 200)])
    db = FakeSrc(events=[UsageEvent(DAY_START + 3, 300)])
    src = CombinedSource([dsh, jsonl, db], now_fn=lambda: DAY_START + 3600)
    events = src.poll()
    assert events == [UsageEvent(DAY_START + 1, 100), UsageEvent(DAY_START + 2, 200), UsageEvent(DAY_START + 3, 300)]


def test_sums_daily_and_total(tmp_path):
    src = CombinedSource([FakeSrc(daily=1000, total=5000), FakeSrc(daily=2000, total=6000)], now_fn=lambda: DAY_START + 3600)
    assert src.daily_total() == 3000
    assert src.total() == 11000


def test_online_any_source(tmp_path):
    assert CombinedSource([FakeSrc(online=False), FakeSrc(online=True)], now_fn=lambda: DAY_START + 3600).online() is True
    assert CombinedSource([FakeSrc(online=False)], now_fn=lambda: DAY_START + 3600).online() is False
    assert CombinedSource([], now_fn=lambda: DAY_START + 3600).online() is False


def test_channel_filter(tmp_path):
    dsh = FakeSrc(daily=1000, total=5000, events=[UsageEvent(DAY_START + 1, 100)])
    claude = FakeSrc(daily=2000, total=6000, events=[UsageEvent(DAY_START + 2, 200)])
    codex = FakeSrc(daily=3000, total=7000, events=[UsageEvent(DAY_START + 3, 300)])
    src = CombinedSource([dsh, claude, codex], names=["dsh", "claude", "codex"], now_fn=lambda: DAY_START + 3600)
    assert src.enabled_channels() == ["dsh", "claude", "codex"]
    assert src.daily_total() == 6000

    # 只显示 codex
    src.set_channel_enabled("dsh", False)
    src.set_channel_enabled("claude", False)
    assert src.enabled_channels() == ["codex"]
    assert src.daily_total() == 3000
    events = src.poll()
    assert events == [UsageEvent(DAY_START + 3, 300)]

    # 显示两个：dsh + claude
    src.set_channel_enabled("dsh", True)
    src.set_channel_enabled("claude", True)
    src.set_channel_enabled("codex", False)
    assert src.enabled_channels() == ["dsh", "claude"]
    assert src.daily_total() == 3000
    assert src.total() == 11000
