"""Claude Code JSONL 直读数据源：秒级 token 消耗（绕过 cc-switch 的 60 秒同步）。

链路：Claude Code 消息完成 → 即时追加一行到 ~/.claude/projects/**/*.jsonl
→ 本模块每秒增量解析新行（type=assistant 且含 message.usage）→ 秒级可见。

口径与 TokenSource 一致：含缓存全量 = input + output + cache_read + cache_creation，
按 created_at（epoch 秒）分桶；当日从本地 0 点起。接口与 TokenSource 相同
（poll / daily_total / online），UI 可无感切换。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from token_running.source import UsageEvent

PROJECTS_DEFAULT = Path.home() / ".claude" / "projects"


def _iso_to_epoch(iso: str) -> int:
    """ISO 8601（如 2026-08-17T14:52:01.968Z）→ epoch 秒。"""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp())


class JsonlSource:
    """增量解析 Claude Code 会话 JSONL 的秒级数据源。

    - 启动时扫描 projects 目录所有 *.jsonl，记录各文件已读字节偏移
    - poll() 只读各文件新增字节并解析；失败静默，下一轮重试
    - daily_total 由解析结果累计，跨天按本地日期重置
    - now_fn 可注入以便测试跨天逻辑
    """

    def __init__(self, projects_dir: str | Path = PROJECTS_DEFAULT, now_fn: Callable[[], float] = time.time) -> None:
        self._dir = Path(projects_dir)
        self._now = now_fn
        self._offsets: dict[Path, int] = {}  # 文件 -> 已读字节偏移
        self._daily_total = 0
        self._grand_total = 0
        self._day_key: str | None = None
        self._online = False
        self._today_since: int | None = None   # 今日口径起点（None=自然日 0 点）
        self._total_since: int | None = None   # 总量口径起点（None=全时段）
        self._init()

    def _init(self) -> None:
        self._day_key = self._today()
        self._daily_total = 0
        self._grand_total = 0
        try:
            if self._dir.exists():
                for f in self._dir.rglob("*.jsonl"):
                    self._offsets[f] = 0  # 起点：首次 poll 全量解析（填充窗口 + 累计当日）
                self._online = True
        except OSError:
            self._online = False

    def set_windows(self, today_since: int | None = None, total_since: int | None = None) -> None:
        """设置统计口径窗口起点；之后全量重扫文件重算累计。"""
        self._today_since = today_since
        self._total_since = total_since
        self._rescan()

    def _rescan(self) -> None:
        """按当前窗口全量重扫所有文件，重算 daily_total / grand_total。"""
        if not self._dir.exists():
            self._daily_total = 0
            self._grand_total = 0
            return
        day_start = self._today_since if self._today_since is not None else self._day_start()
        total_start = self._total_since if self._total_since is not None else 0
        d = t = 0
        for f in self._dir.rglob("*.jsonl"):
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        ev = self._parse_line(line)
                        if ev is None:
                            continue
                        if ev.ts >= total_start:
                            t += ev.tokens
                            if ev.ts >= day_start:
                                d += ev.tokens
            except OSError:
                continue
        self._daily_total = d
        self._grand_total = t

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self._now()))

    def _day_start(self) -> int:
        t = time.localtime(self._now())
        return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))

    def online(self) -> bool:
        return self._online

    def poll(self) -> list[UsageEvent]:
        if not self._online:
            self._init()
            if not self._online:
                return []
        today = self._today()
        if today != self._day_key:  # 跨天：清空当日累计，重新扫描
            self._day_key = today
            self._daily_total = 0
            self._offsets.clear()
            try:
                if self._dir.exists():
                    for f in self._dir.rglob("*.jsonl"):
                        self._offsets[f] = 0
            except OSError:
                pass

        events: list[UsageEvent] = []
        try:
            for f in list(self._offsets):
                offset = self._offsets[f]
                try:
                    size = f.stat().st_size
                    if size < offset:  # 文件被截断/重建：重置偏移
                        offset = 0
                    if size == offset:
                        continue
                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(offset)
                        new_text = fh.read()
                    self._offsets[f] = size
                except OSError:
                    continue
                for line in new_text.splitlines():
                    if not line.strip():
                        continue
                    ev = self._parse_line(line)
                    if ev is not None:
                        events.append(ev)
        except OSError:
            return events

        # 按秒分桶聚合 + 累计当日（只统计今日）；总量累计全部（含历史日）
        day_start = self._day_start()
        bucketed: dict[int, int] = {}
        for ev in events:
            self._grand_total += ev.tokens
            if ev.ts >= day_start:
                bucketed[ev.ts] = bucketed.get(ev.ts, 0) + ev.tokens
                self._daily_total += ev.tokens
        return [UsageEvent(ts, tokens) for ts, tokens in sorted(bucketed.items())]

    def _parse_line(self, line: str) -> UsageEvent | None:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None
        if obj.get("type") != "assistant":
            return None
        usage = obj.get("message", {}).get("usage")
        if not usage or not isinstance(usage, dict):
            return None
        ts_iso = obj.get("timestamp")
        if not ts_iso:
            return None
        try:
            ts = _iso_to_epoch(ts_iso)
        except (ValueError, TypeError, OverflowError):
            return None
        it = int(usage.get("input_tokens") or 0)
        ot = int(usage.get("output_tokens") or 0)
        crt = int(usage.get("cache_read_input_tokens") or 0)
        cct = int(usage.get("cache_creation_input_tokens") or 0)
        tokens = it + ot + crt + cct
        model = obj.get("message", {}).get("model") or ""
        return UsageEvent(ts, tokens, model=model, input_t=it, output_t=ot,
                          cache_read_t=crt, cache_create_t=cct)

    def daily_total(self) -> int:
        return self._daily_total

    def total(self) -> int:
        """全时段累计消耗（含历史日，不随跨天重置）。"""
        return self._grand_total
