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
    model: str = ""            # 模型名（费用计算用；未知为空）
    input_t: int = 0           # 输入 tokens
    output_t: int = 0          # 输出 tokens
    cache_read_t: int = 0      # 缓存命中 tokens
    cache_create_t: int = 0    # 缓存写入 tokens


class TokenSource:
    """只读轮询 cc-switch 数据库，增量拉取新请求记录。

    - 启动时建立连接、记录 max(rowid) 并从今日 0 点聚合当日累计
    - poll() 拉取 rowid 增量并按行累计当日；失败静默，下一轮重试
    - now_fn 可注入以便测试跨天逻辑
    """

    def __init__(self, db_path: str | Path = DB_DEFAULT, now_fn: Callable[[], float] = time.time,
                 app_types: list[str] | None = None) -> None:
        self._db_path = db_path
        self._now = now_fn
        self._app_types = app_types  # None=全部；否则只统计指定 app_type（供合并源排除 claude 避免与 JSONL 重复）
        self._conn: sqlite3.Connection | None = None
        self._last_rowid = 0
        self._daily_total = 0
        self._day_key: str | None = None
        self._online = False
        self._today_since: int | None = None   # 今日口径起点（None=本地自然日 0 点）
        self._total_since: int | None = None   # 总量口径起点（None=全时段）
        self._has_model = False                # 库是否有 model 列（兼容旧测试库）
        self._init()

    def set_windows(self, today_since: int | None = None, total_since: int | None = None) -> None:
        """设置统计口径窗口起点（epoch 秒；None=默认：今日=自然日0点，总量=全时段）。"""
        self._today_since = today_since
        self._total_since = total_since

    def _type_clause(self) -> tuple[str, list]:
        if self._app_types is None:
            return "", []
        ph = ",".join("?" for _ in self._app_types)
        return f" AND app_type IN ({ph})", list(self._app_types)

    def _cols(self) -> str:
        """动态 SELECT 列：有 model 列则带上（费用计算用）。"""
        if self._has_model:
            return _SQL_COLS.replace("rowid, created_at,", "rowid, created_at, model,")
        return _SQL_COLS

    def _init(self) -> None:
        try:
            self._conn = sqlite3.connect(str(self._db_path))
            # 检测 model 列是否存在（旧测试库可能没有）
            try:
                cols = [r[1] for r in self._conn.execute("PRAGMA table_info(proxy_request_logs)").fetchall()]
                self._has_model = "model" in cols
            except sqlite3.Error:
                self._has_model = False
            clause, args = self._type_clause()
            row = self._conn.execute(
                f"SELECT MAX(rowid), COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_creation_tokens),0)"
                f" FROM proxy_request_logs WHERE created_at >= ?{clause}",
                [self._day_start()] + args,
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
            clause, args = self._type_clause()
            cur = self._conn.execute(
                f"SELECT {self._cols()} FROM proxy_request_logs WHERE rowid > ?{clause} ORDER BY rowid",
                [self._last_rowid] + args,
            )
            rows = cur.fetchall()
        except sqlite3.Error:
            return []
        events: list[UsageEvent] = []
        for row in rows:
            if self._has_model:
                rowid, ts, model, i, o, cr, cc = row
            else:
                rowid, ts, i, o, cr, cc = row
                model = ""
            total = i + o + cr + cc
            events.append(UsageEvent(ts, total, model=model or "", input_t=i, output_t=o,
                                     cache_read_t=cr, cache_create_t=cc))
            self._last_rowid = rowid
            self._daily_total += total
        return events

    def daily_total(self) -> int:
        # 默认返回增量维护的自然日累计；设置了自定义今日口径时按窗口重查
        if self._today_since is not None:
            return self._window_sum(self._today_since)
        return self._daily_total

    def _window_sum(self, since: int | None) -> int:
        if not self._online:
            self._init()
        if not self._conn:
            return 0
        try:
            clause, args = self._type_clause()
            if since is None:
                where = "WHERE 1=1"
            else:
                where = "WHERE created_at >= ?"
                args = [since] + args
            row = self._conn.execute(
                f"SELECT COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_creation_tokens),0)"
                f" FROM proxy_request_logs {where}{clause}",
                args,
            ).fetchone()
            return int(row[0])
        except sqlite3.Error:
            return 0

    def total(self) -> int:
        """总量口径累计（默认全时段；可经 set_windows 改为近 N 天）。"""
        return self._window_sum(self._total_since)
