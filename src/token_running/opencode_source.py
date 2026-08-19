"""Opencode CLI 原始日志数据源：秒级 token 消耗（直接读 opencode.db，绕过 cc-switch）。

链路：Opencode 每条 assistant 消息完成 → 即时写入 ~/.local/share/opencode/opencode.db
的 message 表（data 为 JSON 字符串，含 tokens/modelID/time.completed）→ 本模块每秒按
rowid 增量拉取 → 秒级可见。

口径与其它源一致：含缓存全量 = input + output + reasoning + cache_read + cache_write
（opencode 的 tokens.total 即该口径），按 time.completed（epoch 秒）分桶；当日从北京
0 点起。接口与 TokenSource 相同（poll / daily_total / total / online / set_windows）。

message.data 结构：
{"parentID":..., "role":"assistant", "cost":0.0002631832,
 "tokens":{"total":14076,"input":1435,"output":97,"reasoning":0,
           "cache":{"write":0,"read":12544}},
 "modelID":"deepseek-v4-flash","providerID":"deepseek",
 "time":{"created":1785940849549,"completed":1785940851175}, ...}
total = input + output + reasoning + cache.read + cache.write（已验证）。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Callable

from token_running.source import UsageEvent, beijing_day_start, beijing_today

DB_DEFAULT = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


class OpencodeSource:
    """增量解析 opencode.db message 表的秒级数据源。

    - 启动时连接 DB，记录 MAX(rowid) 并从今日 0 点聚合当日累计
    - poll() 拉取 rowid 增量并解析 data JSON（只取含 tokens 的 assistant 消息）
    - daily_total 由解析结果累计，跨天按北京日期重置
    - now_fn 可注入以便测试跨天逻辑
    """

    def __init__(self, db_path: str | Path = DB_DEFAULT, now_fn: Callable[[], float] = time.time) -> None:
        self._db_path = Path(db_path)
        self._now = now_fn
        self._conn: sqlite3.Connection | None = None
        self._last_rowid = 0
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
            if not self._db_path.exists():
                self._conn = None
                self._online = False
                return
            self._conn = sqlite3.connect(str(self._db_path))
            # 校验 message 表存在
            self._conn.execute("SELECT 1 FROM message LIMIT 1").fetchone()
            # 起始 rowid=0：首次 poll 全量重放历史（填充窗口 + 累计当日/总量）
            self._last_rowid = 0
            self._online = True
        except sqlite3.Error:
            self._conn = None
            self._online = False

    def set_windows(self, today_since: int | None = None, total_since: int | None = None) -> None:
        """设置统计口径窗口起点；之后全量重扫重算累计。"""
        self._today_since = today_since
        self._total_since = total_since
        self._rescan()

    def _rescan(self) -> None:
        """按当前窗口全量重扫 message 表，重算 daily_total / grand_total。"""
        if not self._conn:
            return
        day_start = self._today_since if self._today_since is not None else self._day_start()
        total_start = self._total_since if self._total_since is not None else 0
        d = t = 0
        try:
            cur = self._conn.execute("SELECT data FROM message ORDER BY rowid")
            for (data,) in cur:
                ev = self._parse_data(data)
                if ev is None:
                    continue
                if ev.ts >= total_start:
                    t += ev.tokens
                    if ev.ts >= day_start:
                        d += ev.tokens
        except (sqlite3.Error, OSError):
            pass
        self._daily_total = d
        self._grand_total = t

    def _today(self) -> str:
        return beijing_today(self._now())

    def _day_start(self) -> int:
        return beijing_day_start(self._now())

    def online(self) -> bool:
        return self._online

    def poll(self) -> list[UsageEvent]:
        if not self._online:
            self._init()
            if not self._online:
                return []
        today = self._today()
        if today != self._day_key:  # 跨天：重置当日累计（增量继续）
            self._day_key = today
            self._daily_total = 0
        events: list[UsageEvent] = []
        try:
            cur = self._conn.execute(
                "SELECT rowid, data FROM message WHERE rowid > ? ORDER BY rowid",
                [self._last_rowid],
            )
            rows = cur.fetchall()
        except sqlite3.Error:
            return []
        day_start = self._today_since if self._today_since is not None else self._day_start()
        for rowid, data in rows:
            self._last_rowid = rowid
            ev = self._parse_data(data)
            if ev is None:
                continue
            self._grand_total += ev.tokens
            if ev.ts >= day_start:
                self._daily_total += ev.tokens
            events.append(ev)
        return events

    def _parse_data(self, data: str) -> UsageEvent | None:
        try:
            obj = json.loads(data) if isinstance(data, str) else data
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
        if not isinstance(obj, dict):
            return None
        if obj.get("role") != "assistant":
            return None
        tokens = obj.get("tokens")
        if not tokens or not isinstance(tokens, dict):
            return None
        tm = obj.get("time") or {}
        completed = tm.get("completed")
        if not completed:
            return None
        try:
            ts = int(completed) // 1000
        except (ValueError, TypeError):
            return None
        cache = tokens.get("cache") or {}
        it = int(tokens.get("input") or 0)
        ot = int(tokens.get("output") or 0)
        rt = int(tokens.get("reasoning") or 0)
        crt = int(cache.get("read") or 0)
        cct = int(cache.get("write") or 0)
        total = int(tokens.get("total") or 0)
        if total <= 0:
            total = it + ot + rt + crt + cct
        # 与 DSH 口径一致：reasoning 计入总量，费用按 input/output/cache_read 计
        tokens_all = total
        model = obj.get("modelID") or ""
        return UsageEvent(ts, tokens_all, model=model, input_t=it, output_t=ot,
                          cache_read_t=crt, cache_create_t=cct)

    def daily_total(self) -> int:
        return self._daily_total

    def total(self) -> int:
        """全时段累计消耗（含历史日，不随跨天重置）。"""
        return self._grand_total
