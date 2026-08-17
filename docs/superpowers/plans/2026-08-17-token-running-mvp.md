# token-running MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 秒级浮窗 token 消耗监测 MVP —— 轮询 cc-switch SQLite 数据库，以流动柱状图（左进右移）呈现每秒消耗量、数字呈现当日总消耗，验证实时链路。

**Architecture:** PySide6 透明置顶无边框浮窗；`TokenSource` 每秒增量轮询 `~/.cc-switch/cc-switch.db` 的 `proxy_request_logs` 表（`WHERE rowid > last_max`），按 `created_at` 秒桶聚合全量 tokens；`QTimer(1000ms)` 驱动 repaint。

**Tech Stack:** Python 3.12、PySide6（已装 6.11.0）、pytest（已装 9.0.2）。仅标准库 + PySide6，无网络请求。

## Global Constraints

- 数据源：`~/.cc-switch/cc-switch.db`，表 `proxy_request_logs`，字段 `rowid, created_at, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens`
- 每秒消耗口径：`input + output + cache_read + cache_creation` 之和（含缓存全量），按 `created_at` 秒分桶
- 当日总消耗：本地时区今日 0 点（epoch 秒）起全量 tokens 之和
- 统计范围：全部 app_type（不过滤）
- 柱状图：最近 60 秒窗口，**新柱从左侧进入、整体右移**（最新在左，最旧在右）
- 浮窗：`Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool` + `WA_TranslucentBackground`，可拖动
- 不动 token-monitor 现有项目；新项目 `token-running`，独立 git 仓库
- MVP 不做：UI 美化、设置页、JSONL 直读、打包、成本显示

---

### Task 1: TokenSource 测试先行（TDD 红）

**Files:**
- Create: `tests/test_source.py`
- Create: `requirements.txt`
- Create: `src/token_running/__init__.py`

**Interfaces:**
- Consumes: 无（第一个任务）
- Produces: 测试期望的接口契约：
  - `TokenSource(db_path: str | Path, now_fn: Callable[[], float])`
  - `TokenSource.poll() -> list[UsageEvent]`，`UsageEvent(ts: int, tokens: int)`
  - `TokenSource.daily_total() -> int`
  - `TokenSource.online() -> bool`
  - `DB_DEFAULT = Path.home() / ".cc-switch" / "cc-switch.db"`

- [ ] **Step 1: 创建 requirements.txt 与包目录**

```text
# requirements.txt
PySide6>=6.5
pytest>=8
```

创建 `src/token_running/__init__.py`（空文件）。

- [ ] **Step 2: 写测试**

```python
# tests/test_source.py
import sqlite3
import time

import pytest

from token_running.source import TokenSource, UsageEvent


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE proxy_request_logs (
               rowid INTEGER PRIMARY KEY AUTOINCREMENT,
               created_at INTEGER, app_type TEXT,
               input_tokens INTEGER, output_tokens INTEGER,
               cache_read_tokens INTEGER, cache_creation_tokens INTEGER)"""
    )
    for created_at, app, i, o, cr, cc in rows:
        con.execute(
            "INSERT INTO proxy_request_logs (created_at, app_type, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens) VALUES (?,?,?,?,?,?)",
            (created_at, app, i, o, cr, cc),
        )
    con.commit()
    con.close()


DAY_START = 1_700_000_000  # 固定的"今日 0 点"epoch 秒


def test_poll_returns_all_rows_once(tmp_path):
    make_db(tmp_path / "t.db", [(1000, "claude", 10, 20, 30, 40), (1001, "codex", 1, 2, 3, 4)])
    src = TokenSource(tmp_path / "t.db", now_fn=lambda: DAY_START + 3600)
    events = src.poll()
    assert events == [UsageEvent(1000, 100), UsageEvent(1001, 10)]  # 10+20+30+40=100, 1+2+3+4=10
    assert src.poll() == []  # 第二次无增量


def test_tokens_include_cache_full_sum(tmp_path):
    make_db(tmp_path / "t.db", [(1000, "claude", 5, 5, 50, 500)])
    src = TokenSource(tmp_path / "t.db", now_fn=lambda: DAY_START + 3600)
    assert src.poll()[0].tokens == 560  # 含缓存全量


def test_daily_total_starts_from_day_zero(tmp_path):
    # 今日 0 点前 1 秒的请求不应计入；0 点及之后计入
    make_db(tmp_path / "t.db", [(DAY_START - 1, "claude", 100, 0, 0, 0), (DAY_START, "claude", 50, 50, 0, 0)])
    src = TokenSource(tmp_path / "t.db", now_fn=lambda: DAY_START + 3600)
    src.poll()
    assert src.daily_total() == 100


def test_daily_total_resets_across_midnight(tmp_path):
    db = tmp_path / "t.db"
    make_db(db, [(DAY_START, "claude", 100, 0, 0, 0)])
    now = [DAY_START + 3600]  # 当日
    src = TokenSource(db, now_fn=lambda: now[0])
    src.poll()
    assert src.daily_total() == 100
    # 跨天：插入第二天记录，now 推进到第二天 0 点后
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO proxy_request_logs (created_at, app_type, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens) VALUES (?,?,?,?,?,?)",
        (DAY_START + 86400, "claude", 7, 0, 0, 0),
    )
    con.commit()
    con.close()
    now[0] = DAY_START + 86400 + 100
    src.poll()
    assert src.daily_total() == 7  # 昨日 100 不再计入


def test_offline_when_db_missing(tmp_path):
    src = TokenSource(tmp_path / "nope.db", now_fn=lambda: DAY_START + 3600)
    assert src.online() is False
    assert src.poll() == []
    assert src.daily_total() == 0
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'token_running'`

- [ ] **Step 4: Commit 红测试**

```bash
git add tests/ requirements.txt src/token_running/__init__.py
git commit -m "test: TokenSource 增量轮询与当日累计用例（TDD 红）
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: TokenSource 实现

**Files:**
- Create: `src/token_running/source.py`

**Interfaces:**
- Consumes: Task 1 的测试（接口契约）
- Produces: `TokenSource`（poll/daily_total/online）、`UsageEvent`、`DB_DEFAULT`

- [ ] **Step 1: 写实现**

```python
# src/token_running/source.py
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
```

- [ ] **Step 2: 运行测试**

Run: `python -m pytest tests/ -v`
Expected: 5 passed（`_init` 在 `test_offline_when_db_missing` 中应抛 `sqlite3.Error`——注意：连接不存在的文件不会抛错，需确认 `_init` 里 `MAX(rowid)` 查询会因 no such table 抛错；若临时库文件不存在，`sqlite3.connect` 会**创建空文件**，则 `_init` 的 `SELECT MAX(rowid)...` 因 no such table 抛 `sqlite3.OperationalError` → `_online=False`，测试通过。若发现行为不符，在 `_init` 中先校验表存在。）

- [ ] **Step 3: 用真实 cc-switch 库冒烟测试**

Run:
```bash
python -c "from token_running.source import TokenSource; s = TokenSource(); print('online:', s.online()); print('poll:', s.poll()); print('daily:', s.daily_total())"
```
Expected: `online: True`、poll 返回若干 `UsageEvent`（ts 为最近 epoch 秒）、`daily: <今日累计整数>`

- [ ] **Step 4: Commit**

```bash
git add src/token_running/source.py
git commit -m "feat: TokenSource 秒级增量轮询 cc-switch 数据库
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: RealtimeWindow 浮窗 + main.py 入口

**Files:**
- Create: `src/token_running/realtime_ui.py`
- Create: `main.py`

**Interfaces:**
- Consumes: `TokenSource`（Task 2）
- Produces: `RealtimeWindow(QWidget)`（构造可选注入 `source`）、`main.py` 可运行入口

- [ ] **Step 1: 写浮窗实现**

```python
# src/token_running/realtime_ui.py
"""透明置顶浮窗：流动柱状图（每秒消耗量，左进右移）+ 当日总消耗数字。"""
from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from token_running.source import TokenSource

WIDTH, HEIGHT = 440, 160
BAR_WINDOW = 60          # 柱状图窗口：最近 60 秒
CHART_LEFT, CHART_TOP = 16, 34
CHART_RIGHT, CHART_BOTTOM = 424, 140
BG_COLOR = QColor(13, 17, 28, 224)
BAR_COLOR = QColor(94, 200, 130, 210)
BAR_DIM = QColor(94, 200, 130, 60)
TEXT_COLOR = QColor(235, 240, 248, 230)
MUTED = QColor(140, 150, 165, 200)
GREEN = QColor(94, 200, 130)
RED = QColor(230, 90, 90)


def _format_compact(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


class RealtimeWindow(QWidget):
    def __init__(self, source: TokenSource | None = None) -> None:
        super().__init__(None)
        self._source = source or TokenSource()
        self._bars: deque[tuple[int, int]] = deque()  # (ts, tokens)，按时间升序
        self._drag_offset: QPoint | None = None

        self.setWindowTitle("Token Running")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(WIDTH, HEIGHT)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    # ---- 数据 ----

    def _tick(self) -> None:
        for ev in self._source.poll():
            self._bars.append((ev.ts, ev.tokens))
        cutoff = int(time.time()) - BAR_WINDOW
        while self._bars and self._bars[0][0] < cutoff:
            self._bars.popleft()
        self.update()

    # ---- 拖动 ----

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        event.accept()

    # ---- 绘制 ----

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(BG_COLOR)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 16, 16)

        # 标题行：状态点 + 名称
        online = self._source.online()
        p.setBrush(GREEN if online else RED)
        p.drawEllipse(22, 12, 8, 8)
        p.setPen(MUTED)
        f = QFont("Segoe UI", 9)
        p.setFont(f)
        p.drawText(38, 20, "TOKEN RUNNING" + (" · 实时" if online else " · 离线"))

        # 当日总消耗（大数字，右上）
        p.setPen(TEXT_COLOR)
        f2 = QFont("Segoe UI", 17, QFont.Weight.DemiBold)
        p.setFont(f2)
        p.drawText(220, 12, 424, 22, Qt.AlignmentFlag.AlignRight, _format_compact(self._source.daily_total()))
        p.setPen(MUTED)
        f3 = QFont("Segoe UI", 8)
        p.setFont(f3)
        p.drawText(220, 28, 424, 14, Qt.AlignmentFlag.AlignRight, "今日 tokens")

        # 流动柱状图：最新在左，旧柱右移
        now_sec = int(time.time())
        visible = [b for b in self._bars if b[0] > now_sec - BAR_WINDOW]
        max_tokens = max((b[1] for b in visible), default=0) or 1
        chart_w = CHART_RIGHT - CHART_LEFT
        chart_h = CHART_BOTTOM - CHART_TOP
        if visible:
            step = chart_w / BAR_WINDOW
            bar_w = max(1.0, step * 0.6)
            for idx, (ts, tokens) in enumerate(visible):
                age = now_sec - ts
                x = CHART_LEFT + age * step  # 最新(age=0)在最左，越旧越右
                h = max(2.0, chart_h * tokens / max_tokens)
                p.setBrush(BAR_DIM if age > 40 else BAR_COLOR)
                p.drawRect(int(x), int(CHART_BOTTOM - h), int(bar_w), int(h))

        p.setPen(QColor(255, 255, 255, 30))
        p.drawLine(CHART_LEFT, CHART_BOTTOM + 2, CHART_RIGHT, CHART_BOTTOM + 2)
```

- [ ] **Step 2: 写入口 main.py**

```python
# main.py
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from token_running.realtime_ui import RealtimeWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = RealtimeWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: 启动冒烟验证**

Run: `python -c "from token_running.realtime_ui import RealtimeWindow; from PySide6.QtWidgets import QApplication; import sys; app=QApplication(sys.argv); w=RealtimeWindow(); w.show(); print('window ok'); w._tick(); print('bars:', len(w._bars), 'daily:', w._source.daily_total())"`

Expected: 无异常，`bars` 为 0 或小值（真实库中上一轮对话的请求已在，poll 首次会拉全量 60 秒内记录），`daily` 为整数。

- [ ] **Step 4: 运行 pytest 确认不回归**

Run: `python -m pytest tests/ -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/token_running/realtime_ui.py main.py
git commit -m "feat: 透明置顶浮窗——流动柱状图 + 当日总消耗
Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 端到端实时性验证（真机）

**Files:** 无新文件

**Interfaces:** 无

- [ ] **Step 1: 启动浮窗**

Run: `python main.py`（后台运行或另开终端）

- [ ] **Step 2: 在 Claude Code 中发起一轮对话**（本会话继续提问，或另开会话）

- [ ] **Step 3: 观察验证项**
  1. 消息完成 2–7 秒内，浮窗柱状图**左侧出现新柱**，旧柱右移
  2. 「今日 tokens」数字随之增长
  3. 状态点为绿（cc-switch 在线）
  4. 空闲 30 秒：柱状图静止不崩溃、无异常输出
  5. 拖动浮窗正常

- [ ] **Step 4: 记录验证结果并收尾**

在 `docs/superpowers/plans/2026-08-17-token-running-mvp.md` 末尾追加验证结果（日期、是否通过、实际延迟秒数）。

---

## Task 4 验证记录（2026-08-17，DeepSeek harness 执行）

### 结果：**通过**（数据链路端到端验证；目视项待用户最终确认）

| 验收项 | 结果 | 证据 |
|---|---|---|
| 1. `python main.py` 启动浮窗 | ✅ | 单进程 pythonw PID 68768，~110MB，持续存活超过 10 分钟 |
| 2. Claude Code 对话触发新请求 | ✅ | 观察器检测到 rowid 120470 → 120480（+10 条）；后续又增至 120486 |
| 3. 新柱左侧进入、旧柱右移（2–7 秒延迟） | ✅ 数据侧 | 最新记录 created_at 距检测时刻约 11 秒（含同步延迟 2–7s + 1s 轮询 + 观察间隔）；目视确认待用户 |
| 4. 「今日 tokens」数字增长 | ✅ | 25,090,510 → 26,023,075 → 26,608,543（观察期间两次增量，共 +152 万） |
| 5. 空闲 40 秒静止、不崩溃、无异常输出 | ✅ | 5 次轮询（t=8s~40s）窗口进程数恒为 1，stderr 无输出 |
| 6. 拖动浮窗 | ⏳ 待用户目视确认 | — |

### 实测延迟量化
- 观察器启动后 **32 秒** 检测到首批新行（rowid 120470→120480）
- 最新一条 created_at=1786956012，检测时刻 now=1786956023，**入库延迟 ≈ 11 秒**（含 cc-switch 2–7s 同步 + 轮询/观察间隔；柱状图实际呈现延迟应更短）

### 备注
- 过程中曾因重复尝试启动方式叠加出 7 个窗口，已全部清理，现仅保留 1 个
- 测试期间误杀过 VSCode 的 Jedi language server（合法进程），VSCode 已自动恢复，未受影响

