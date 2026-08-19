"""Codex CLI 原始日志数据源：秒级 token 消耗（直接读 rollout JSONL，绕过 cc-switch）。

链路：Codex CLI 每次 API 请求 → 即时追加一行到
~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
→ 本模块每秒增量解析新行（type=event_msg 且 payload.type=token_count）→ 秒级可见。

口径与其它源一致：含缓存全量 = 付费输入 + 输出 + 缓存命中 + 缓存写入，
按事件 timestamp（epoch 秒）分桶；当日从北京 0 点起。接口与 TokenSource 相同
（poll / daily_total / total / online / set_windows），UI 可无感切换。

rollout 行结构（Codex Rust CLI）：
{"timestamp":"2026-08-18T13:35:24.489Z","type":"event_msg",
 "payload":{"type":"token_count",
   "info":{"total_token_usage":{...},     # 会话累计
           "last_token_usage":{"input_tokens":17790,"cached_input_tokens":3840,
                               "cache_write_input_tokens":0,"output_tokens":140,
                               "reasoning_output_tokens":62,"total_tokens":17930}}}}
第 0 行 type=session_meta 带顶层 "model":"gpt-5.6-terra"（每文件模型）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from token_running.source import UsageEvent, beijing_day_start, beijing_today

SESSIONS_DEFAULT = Path.home() / ".codex" / "sessions"


def _iso_to_epoch(iso: str) -> int:
    """ISO 8601（如 2026-08-18T13:35:24.489Z）→ epoch 秒。"""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp())


class CodexSource:
    """增量解析 Codex CLI rollout JSONL 的秒级数据源。

    - 启动时扫描 sessions 目录所有 rollout-*.jsonl，记录各文件已读字节偏移
    - poll() 只读各文件新增字节并解析；失败静默，下一轮重试
    - daily_total 由解析结果累计，跨天按北京日期重置
    - now_fn 可注入以便测试跨天逻辑
    """

    def __init__(self, sessions_dir: str | Path = SESSIONS_DEFAULT, now_fn: Callable[[], float] = time.time) -> None:
        self._dir = Path(sessions_dir)
        self._now = now_fn
        self._offsets: dict[Path, int] = {}  # 文件 -> 已读字节偏移
        self._model_by_file: dict[Path, str] = {}  # 文件 -> 最近模型
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
                for f in self._dir.rglob("rollout-*.jsonl"):
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
        for f in self._dir.rglob("rollout-*.jsonl"):
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        ev = self._parse_line(line, f)
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
        if today != self._day_key:  # 跨天：清空当日累计，重新扫描
            self._day_key = today
            self._daily_total = 0
            self._offsets.clear()
            try:
                if self._dir.exists():
                    for f in self._dir.rglob("rollout-*.jsonl"):
                        self._offsets[f] = 0
            except OSError:
                pass

        # 每次 poll 扫描目录，发现运行期间新建的会话文件
        try:
            if self._dir.exists():
                for f in self._dir.rglob("rollout-*.jsonl"):
                    if f not in self._offsets:
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
                    # 只推进到最后一个完整换行（避免读入未写完的半行导致丢失）
                    complete_end = new_text.rfind("\n")
                    if complete_end < 0:
                        continue  # 无完整行，等待下轮
                    self._offsets[f] = offset + complete_end + 1
                    new_text = new_text[:complete_end]
                except OSError:
                    continue
                for line in new_text.splitlines():
                    if not line.strip():
                        continue
                    ev = self._parse_line(line, f)
                    if ev is not None:
                        events.append(ev)
        except OSError:
            return events

        # 按秒分桶聚合，保留 token 明细（费用计算依赖 model/input/output/cache_read）
        day_start = self._today_since if self._today_since is not None else self._day_start()
        bucketed: dict[int, dict] = {}
        for ev in events:
            self._grand_total += ev.tokens
            if ev.ts >= day_start:
                self._daily_total += ev.tokens
            agg = bucketed.setdefault(ev.ts, {"tokens": 0, "input_t": 0, "output_t": 0,
                                              "cache_read_t": 0, "cache_create_t": 0, "model": ev.model})
            agg["tokens"] += ev.tokens
            agg["input_t"] += ev.input_t
            agg["output_t"] += ev.output_t
            agg["cache_read_t"] += ev.cache_read_t
            agg["cache_create_t"] += ev.cache_create_t
        out = []
        for ts in sorted(bucketed):
            a = bucketed[ts]
            out.append(UsageEvent(ts, a["tokens"], model=a["model"], input_t=a["input_t"],
                                  output_t=a["output_t"], cache_read_t=a["cache_read_t"],
                                  cache_create_t=a["cache_create_t"]))
        return out

    def _parse_line(self, line: str, f: Path | None = None) -> UsageEvent | None:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None
        etype = obj.get("type")
        if etype == "session_meta":  # 会话元数据：携带模型
            prov = (obj.get("payload") or {}).get("base_instructions", {}).get("provenance") or {}
            m = prov.get("model")
            if m and f is not None:
                self._model_by_file[f] = m
            return None
        if etype == "turn_context":  # 切换模型时更新（payload.model 为当前 turn 模型）
            m = (obj.get("payload") or {}).get("model")
            if m and f is not None:
                self._model_by_file[f] = m
            return None
        if etype != "event_msg":
            return None
        payload = obj.get("payload") or {}
        if payload.get("type") != "token_count":
            return None
        info = payload.get("info") or {}
        usage = info.get("last_token_usage")
        if not usage or not isinstance(usage, dict):
            return None
        ts_iso = obj.get("timestamp")
        if not ts_iso:
            return None
        try:
            ts = _iso_to_epoch(ts_iso)
        except (ValueError, TypeError, OverflowError):
            return None
        input_all = int(usage.get("input_tokens") or 0)
        cached = int(usage.get("cached_input_tokens") or 0)
        ot = int(usage.get("output_tokens") or 0)
        cct = int(usage.get("cache_write_input_tokens") or 0)
        it = input_all - cached          # 付费输入 = 总输入 - 缓存命中
        crt = cached                     # 缓存命中按缓存价
        tokens = it + ot + crt + cct
        model = self._model_by_file.get(f) if f is not None else ""
        return UsageEvent(ts, tokens, model=model, input_t=it, output_t=ot,
                          cache_read_t=crt, cache_create_t=cct)

    def daily_total(self) -> int:
        return self._daily_total

    def total(self) -> int:
        """全时段累计消耗（含历史日，不随跨天重置）。"""
        return self._grand_total
