"""DeepSeek Harness 会话轨迹数据源：秒级 token 消耗（直接读 DSH 的 session*.jsonl.zstd）。

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

    - 启动时扫描所有 session*.jsonl.zstd；首次 poll 全量解析（填充窗口 + 累计当日/总量）
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
        self._today_since: int | None = None   # 今日口径起点（None=自然日 0 点）
        self._total_since: int | None = None   # 总量口径起点（None=全时段）
        self._model_by_file: dict[Path, str] = {}  # 每个轨迹文件当前模型（避免多文件交叉污染）
        self._init()

    def set_windows(self, today_since: int | None = None, total_since: int | None = None) -> None:
        """设置统计口径窗口起点；之后全量重扫轨迹重算累计。"""
        self._today_since = today_since
        self._total_since = total_since
        self._rescan()

    def _rescan(self) -> None:
        if not self._dir.exists():
            self._daily_total = 0
            self._grand_total = 0
            return
        day_start = self._today_since if self._today_since is not None else self._day_start()
        total_start = self._total_since if self._total_since is not None else 0
        d = t = 0
        for f in self._dir.rglob("session*.jsonl.zstd"):
            try:
                full = self._decompress(f)
                for line in full.splitlines():
                    ev = self._parse_line(line, f)
                    if ev is None:
                        continue
                    if ev.ts >= total_start:
                        t += ev.tokens
                        if ev.ts >= day_start:
                            d += ev.tokens
            except Exception:
                continue
        self._daily_total = d
        self._grand_total = t

    def _scan_files(self) -> None:
        if not self._dir.exists():
            return
        for f in self._dir.rglob("session*.jsonl.zstd"):
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

        # 每次 poll 扫描目录，发现运行期间新建的会话轨迹
        try:
            self._scan_files()
        except OSError:
            pass

        events: list[UsageEvent] = []
        for f in list(self._parsed_lines):
            try:
                size = f.stat().st_size
            except OSError:
                continue
            last_size = self._last_size.get(f, 0)
            # 文件重建/被替换（变小或同大小但内容变化）：以行数回落检测
            if size < last_size:
                self._parsed_lines[f] = 0
            if size == last_size:
                continue
            try:
                full = self._decompress(f)
            except Exception:
                continue
            lines = full.splitlines()
            parsed = self._parsed_lines.get(f, 0)
            if len(lines) < parsed:  # 内容被替换（行数变少）：全量重扫
                parsed = 0
                self._parsed_lines[f] = 0
            if len(lines) > parsed:
                for line in lines[parsed:]:
                    if not line.strip():
                        continue
                    ev = self._parse_line(line, f)
                    if ev is not None:
                        events.append(ev)
                self._parsed_lines[f] = len(lines)
            self._last_size[f] = size

        # 按秒分桶聚合，保留 token 明细（费用计算依赖 model/input/output/cache_read）
        # 增量累计用有效窗口起点（set_windows 设置的今日口径，否则自然日 0 点）
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
        # request/header 携带模型信息，更新该文件的模型（多文件不交叉污染）
        if etype == "request/header":
            cfg = obj.get("data", {}).get("header", {}).get("config", {})
            m = cfg.get("model")
            if m and f is not None:
                self._model_by_file[f] = m
            return None
        if etype != "assistant/message":
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
        it = int(usage.get("inputTokens") or 0)
        ot = int(usage.get("outputTokens") or 0)
        crt = int(usage.get("cacheReadTokens") or 0)
        rt = int(usage.get("reasoningTokens") or 0)
        tokens = it + ot + crt + rt
        model = self._model_by_file.get(f) if f is not None else ""
        return UsageEvent(ts, tokens, model=model, input_t=it, output_t=ot,
                          cache_read_t=crt, cache_create_t=0)
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

        # request/header 携带模型信息，更新该文件的模型（多文件不交叉污染）
        if etype == "request/header":
            cfg = obj.get("data", {}).get("header", {}).get("config", {})
            m = cfg.get("model")
            if m and f is not None:
                self._model_by_file[f] = m
            return None
        if etype != "assistant/message":
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
        it = int(usage.get("inputTokens") or 0)
        ot = int(usage.get("outputTokens") or 0)
        crt = int(usage.get("cacheReadTokens") or 0)
        rt = int(usage.get("reasoningTokens") or 0)
        tokens = it + ot + crt + rt
        model = self._model_by_file.get(f) if f is not None else ""
        return UsageEvent(ts, tokens, model=model, input_t=it, output_t=ot,
                          cache_read_t=crt, cache_create_t=0)

    def daily_total(self) -> int:
        return self._daily_total

    def total(self) -> int:
        """全时段累计消耗（含历史日，不随跨天重置）。"""
        return self._grand_total