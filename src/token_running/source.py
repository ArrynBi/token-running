"""cc-switch SQLite 数据源：秒级增量轮询 proxy_request_logs。

数据链路（已实验验证）：
Claude Code 消息完成 → 即时写入 ~/.claude/projects/**/*.jsonl
→ cc-switch 事件驱动 2-7 秒内解析入库 → 本模块每秒轮询增量。
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DB_DEFAULT = Path.home() / ".cc-switch" / "cc-switch.db"

_SQL_COLS = "rowid, created_at, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens"


@dataclass(frozen=True)
class UsageEvent:
    ts: int      # created_at：请求完成时刻（epoch 秒）
    tokens: int  # 全量 tokens = input+output+cache_read+cache_creation


class TokenSource:
    """只读轮询 cc-switch 数据库，增量拉取新请求记录。

    - 启动时建立连接、记录 max(rowid) 并从今日 0 点聚合当日累计
    - poll() 拉取 rowid 增量并按行累计当日；失败静默，下一轮重试
    - now_fn 可注入以便测试跨天逻辑
    """

    def __init__(self, db_path: str | Path = DB_DEFAULT, now_fn: Callable[[], float] = time.time) -> None:
        self._db_path = db_path
        self._now = now_fn
        self._conn: sqlite3.Connection | None = None
        self._last_rowid = 0
        self._daily_total = 0
        self._day_key: str | None = None
        self._online = False
        self._init()

    def _init(self) -> None:
        try:
            self._conn = sqlite3.connect(str(self._db_path))
            row = self._conn.execute(
                f"SELECT MAX(rowid), COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_creation_tokens),0)"
                f" FROM proxy_request_logs WHERE created_at >= ?",
                (self._day_start(),),
            ).fetchone()
            self._last_rowid = row[0] or 0
            self._daily_total = int(row[1])
            self._day_key = self._today()
            self._online = True
        except sqlite3.Error:
            self._conn = None
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
        if today != self._day_key:  # 跨天：按新日期重新聚合今日累计
            self._day_key = today
            try:
                row = self._conn.execute(
                    f"SELECT COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_creation_tokens),0)"
                    f" FROM proxy_request_logs WHERE created_at >= ? AND rowid <= ?",
                    (self._day_start(), self._last_rowid),
                ).fetchone()
                self._daily_total = int(row[0])
            except sqlite3.Error:
                pass
        try:
            cur = self._conn.execute(
                f"SELECT {_SQL_COLS} FROM proxy_request_logs WHERE rowid > ? ORDER BY rowid",
                (self._last_rowid,),
            )
            rows = cur.fetchall()
        except sqlite3.Error:
            return []
        events: list[UsageEvent] = []
        for rowid, ts, i, o, cr, cc in rows:
            total = i + o + cr + cc
            events.append(UsageEvent(ts, total))
            self._last_rowid = rowid
            self._daily_total += total
        return events

    def daily_total(self) -> int:
        return self._daily_total

    def total(self) -> int:
        """全时段累计消耗（DB 全量 SUM，不随跨天重置）；失败返回 0。"""
        if not self._online:
            self._init()
        if not self._conn:
            return 0
        try:
            row = self._conn.execute(
                f"SELECT COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_creation_tokens),0)"
                f" FROM proxy_request_logs"
            ).fetchone()
            return int(row[0])
        except sqlite3.Error:
            return 0
