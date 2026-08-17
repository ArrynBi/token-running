"""DeepSeek Harness 会话轨迹数据源：秒级 token 消耗（直接读 DSH 的 session.jsonl.zstd）。

链路：DSH 每次 assistant 消息完成 → 写入 ~/.dsh/sessions/<workspace>/session-<id>.jsonl.zstd
（zstd 压缩的 JSONL，事件流含 assistant/message 的 usage 字段）→ 本模块增量解析。

口径与其它源一致：全量 = inputTokens + outputTokens + cacheReadTokens + reasoningTokens，
按消息完成时刻（time，epoch 毫秒）转秒分桶；当日从本地 0 点起。
接口与 TokenSource / JsonlSource 相同（poll / daily_total / total / online）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import zstandard

from token_running.source import UsageEvent

TRACES_DEFAULT = Path.home() / ".dsh" / "sessions"


class DeepseekTraceSource:
    """增量解析 DSH 会话轨迹 zstd 的秒级数据源。

    - 启动时扫描所有 session.jsonl.zstd；首次 poll 全量解析（填充窗口 + 累计当日/总量）
    - 之后每轮只解析新增行（文件追加帧 → 重解压后按已解析行数切分）
    - 与 JsonlSource 相同接口；失败静默，下一轮重试
    """

    def __init__(self, traces_dir: str | Path = TRACES_DEFAULT, now_fn: Callable[[], float] = time.time) -> None:
        self._dir = Path(traces_dir)
        self._now = now_fn
        self._parsed_lines: dict[Path, int] = {}  # 文件 -> 已解析行数
        self._last_size: dict[Path, int] = {}     # 文件 -> 上次字节数（判断是否有新增）
        self._daily_total = 0
        self._grand_total = 0
        self._day_key: str | None = None
        self._online = False
        self._init()

    def _scan_files(self) -> None:
        if not self._dir.exists():
            return
        for f in self._dir.rglob("session.jsonl.zstd"):
            if f not in self._parsed_lines:
                self._parsed_lines[f] = 0
                self._last_size[f] = 0

    def _init(self) -> None:
        self._day_key = self._today()
        self._daily_total = 0
        self._grand_total = 0
        try:
            self._scan_files()
            self._online = len(self._parsed_lines) > 0
        except OSError:
            self._online = False

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
        if today != self._day_key:  # 跨天：当日归零，全量重扫（总量按全部历史重算）
            self._day_key = today
            self._daily_total = 0
            self._grand_total = 0
            self._parsed_lines = {}
            self._last_size = {}
            try:
                self._scan_files()
            except OSError:
                return []

        events: list[UsageEvent] = []
        for f in list(self._parsed_lines):
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if size < self._last_size.get(f, 0):  # 文件重建：全量重扫
                self._parsed_lines[f] = 0
            if size == self._last_size.get(f, 0):
                continue
            try:
                full = self._decompress(f)
            except Exception:
                continue
            lines = full.splitlines()
            parsed = self._parsed_lines.get(f, 0)
            if len(lines) > parsed:
                for line in lines[parsed:]:
                    if not line.strip():
                        continue
                    ev = self._parse_line(line)
                    if ev is not None:
                        events.append(ev)
                self._parsed_lines[f] = len(lines)
            self._last_size[f] = size

        day_start = self._day_start()
        bucketed: dict[int, int] = {}
        for ev in events:
            self._grand_total += ev.tokens
            if ev.ts >= day_start:
                bucketed[ev.ts] = bucketed.get(ev.ts, 0) + ev.tokens
                self._daily_total += ev.tokens
        return [UsageEvent(ts, tokens) for ts, tokens in sorted(bucketed.items())]

    def _decompress(self, f: Path) -> str:
        dctx = zstandard.ZstdDecompressor()
        chunks: list[bytes] = []
        with open(f, "rb") as fh:
            reader = dctx.stream_reader(fh)
            while True:
                c = reader.read(1 << 20)
                if not c:
                    break
                chunks.append(c)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _parse_line(self, line: str) -> UsageEvent | None:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None
        if obj.get("type") != "assistant/message":
            return None
        usage = obj.get("data", {}).get("usage")
        if not usage or not isinstance(usage, dict):
            return None
        ts_ms = obj.get("time")
        if not ts_ms:
            return None
        try:
            ts = int(ts_ms) // 1000
        except (ValueError, TypeError):
            return None
        tokens = (
            int(usage.get("inputTokens") or 0)
            + int(usage.get("outputTokens") or 0)
            + int(usage.get("cacheReadTokens") or 0)
            + int(usage.get("reasoningTokens") or 0)
        )
        return UsageEvent(ts, tokens)

    def daily_total(self) -> int:
        return self._daily_total

    def total(self) -> int:
        """全时段累计消耗（含历史日，不随跨天重置）。"""
        return self._grand_total
