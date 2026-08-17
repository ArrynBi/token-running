"""透明置顶浮窗：流动柱状图（每秒消耗量，左进右移）+ 当日总消耗数字。"""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from token_running.combined_source import CombinedSource
from token_running.deepseek_trace import DeepseekTraceSource
from token_running.jsonl_source import JsonlSource
from token_running.source import TokenSource

WIDTH, HEIGHT = 440, 160
BAR_WINDOW = 60          # 柱状图窗口：最近 60 秒
CHART_LEFT, CHART_TOP = 42, 54   # 左侧留 26px 给纵轴刻度，顶部留标题区（含总量行）
CHART_RIGHT, CHART_BOTTOM = 424, 140
BG_COLOR = QColor(13, 17, 28, 252)  # 接近不透明（alpha 252），减少桌面透光导致的时暗时亮
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
        if source is not None:
            self._source = source
        else:
            # 合并源：DSH 轨迹 + Claude JSONL + cc-switch DB（codex/opencode；DB 排除 claude 避免与 JSONL 重复）
            self._source = CombinedSource(
                [
                    DeepseekTraceSource(),
                    JsonlSource(),
                    TokenSource(app_types=["codex", "opencode"]),
                ],
                names=["dsh", "claude", "codex"],
            )
        self._bars_sec: dict[int, int] = {}  # ts(秒) -> 该秒全量 tokens 桶（秒级视图）
        self._bars_5s: dict[int, int] = {}   # 5 秒起点(epoch 秒) -> 该 5 秒窗全量 tokens 桶（5 秒级视图）
        self._bars_min: dict[int, int] = {}  # 分钟起点(epoch 秒) -> 该分钟全量 tokens 桶（分钟视图）
        self._mode: str = "sec"              # "sec" 秒级 60s 窗口 | "5s" 5 秒级 300s 窗口 | "min" 分钟级 60min 窗口
        self._drag_offset: QPoint | None = None

        self.setWindowTitle("Token Running")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFixedSize(WIDTH, HEIGHT)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    # ---- 数据 ----

    def _tick(self) -> None:
        now = int(time.time())
        for ev in self._source.poll():
            self._bars_sec[ev.ts] = self._bars_sec.get(ev.ts, 0) + ev.tokens
            k5 = ev.ts // 5 * 5
            self._bars_5s[k5] = self._bars_5s.get(k5, 0) + ev.tokens
            mkey = ev.ts // 60 * 60
            self._bars_min[mkey] = self._bars_min.get(mkey, 0) + ev.tokens
        # 清理各自窗口外的桶：秒级 60s / 5 秒级 300s / 分钟级 60min
        sec_cutoff = now - BAR_WINDOW
        for ts in [t for t in self._bars_sec if t < sec_cutoff]:
            del self._bars_sec[ts]
        cutoff_5s = (now // 5 * 5) - BAR_WINDOW * 5
        for k in [t for t in self._bars_5s if t < cutoff_5s]:
            del self._bars_5s[k]
        min_cutoff = (now // 60 * 60) - BAR_WINDOW * 60
        for m in [t for t in self._bars_min if t < min_cutoff]:
            del self._bars_min[m]
        self.update()

    # ---- 拖动 ----

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # 左键双击：循环切换 秒级 -> 5秒级 -> 分钟级
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            self._cycle_mode()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            # 右键释放：打开菜单
            self._drag_offset = None
            self._show_menu(event.globalPosition().toPoint())
            event.accept()
            return
        self._drag_offset = None
        event.accept()

    # ---- 模式与菜单 ----

    def _cycle_mode(self) -> None:
        order = ("sec", "5s", "min")
        self._mode = order[(order.index(self._mode) + 1) % len(order)]
        self.update()

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self.update()

    def _set_channel(self, channel: str, enabled: bool) -> None:
        if isinstance(self._source, CombinedSource):
            self._source.set_channel_enabled(channel, enabled)
            # 渠道变更后清空已聚合桶，避免禁用渠道的旧数据残留
            self._bars_sec.clear()
            self._bars_5s.clear()
            self._bars_min.clear()
            self.update()

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for key, label in (("sec", "秒级（60s 窗口）"), ("5s", "5 秒级（300s 窗口）"), ("min", "分钟级（60min 窗口）")):
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(self._mode == key)
            act.triggered.connect(lambda checked=False, k=key: self._set_mode(k))
            group.addAction(act)
            menu.addAction(act)
        menu.addSeparator()
        # 渠道多选：只显示选中的来源（DSH / Claude Code / Codex）
        title_act = QAction("显示渠道", menu)
        title_act.setEnabled(False)
        menu.addAction(title_act)
        if isinstance(self._source, CombinedSource):
            for ch, label in (("dsh", "DSH（DeepSeek）"), ("claude", "Claude Code"), ("codex", "Codex / Opencode")):
                act = QAction(label, menu)
                act.setCheckable(True)
                act.setChecked(self._source.is_channel_enabled(ch))
                act.toggled.connect(lambda checked, c=ch: self._set_channel(c, checked))
                menu.addAction(act)
        menu.addSeparator()
        quit_act = menu.addAction("退出")
        quit_act.triggered.connect(self.close)
        return menu

    def _show_menu(self, pos: QPoint) -> None:
        self._build_menu().exec(pos)

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
        mode_text = {"sec": "秒级", "5s": "5秒级", "min": "分钟级"}.get(self._mode, "秒级")
        source_name = {
            "CombinedSource": "ALL",
            "DeepseekTraceSource": "DSH",
            "JsonlSource": "CLAUDE",
            "TokenSource": "CC-DB",
        }.get(type(self._source).__name__, "?")
        p.drawText(38, 20, f"TOKEN RUNNING · {source_name} · " + ("实时" if online else "离线") + f" · {mode_text}")

        # 右上角：当日大数字 + 今日标签 + 总量（全时段累计）
        p.setPen(TEXT_COLOR)
        f2 = QFont("Segoe UI", 17, QFont.Weight.DemiBold)
        p.setFont(f2)
        p.drawText(220, 12, 424, 22, Qt.AlignmentFlag.AlignRight, _format_compact(self._source.daily_total()))
        p.setPen(MUTED)
        f3 = QFont("Segoe UI", 8)
        p.setFont(f3)
        p.drawText(220, 28, 424, 12, Qt.AlignmentFlag.AlignRight, "今日 tokens")
        p.drawText(220, 41, 424, 12, Qt.AlignmentFlag.AlignRight, f"总量 {_format_compact(self._source.total())}")

        # 流动柱状图：固定 BAR_WINDOW 槽，最新在左（offset=0），旧柱右移；无数据槽画 2px 基线
        now_sec = int(time.time())
        chart_w = CHART_RIGHT - CHART_LEFT
        chart_h = CHART_BOTTOM - CHART_TOP
        step = chart_w / BAR_WINDOW
        bar_w = max(1.0, step * 0.6)
        if self._mode == "min":
            buckets = self._bars_min
            unit_label = "tok/min"
            slot_sec = 60
            cur = now_sec // 60 * 60  # 当前分钟起点
            slot_of = lambda i: cur - i * 60
        elif self._mode == "5s":
            buckets = self._bars_5s
            unit_label = "tok/5s"
            slot_sec = 5
            cur = now_sec // 5 * 5  # 当前 5 秒窗起点
            slot_of = lambda i: cur - i * 5
        else:
            buckets = self._bars_sec
            unit_label = "tok"
            slot_sec = 1
            cur = now_sec
            slot_of = lambda i: cur - i
        max_tokens = max(buckets.values(), default=0) or 1

        # 纵轴刻度（单位随模式）：0 / 一半 / 满刻度
        p.setPen(MUTED)
        fy = QFont("Segoe UI", 7)
        p.setFont(fy)
        p.drawText(2, CHART_TOP - 12, CHART_LEFT - 6, 10, Qt.AlignmentFlag.AlignRight, unit_label)
        for frac, val in [(1.0, max_tokens), (0.5, max_tokens // 2), (0.0, 0)]:
            y = CHART_BOTTOM - int(chart_h * frac)
            p.drawText(2, y - 4, CHART_LEFT - 6, 10, Qt.AlignmentFlag.AlignRight, _format_compact(val))
        # 刻度线
        p.setPen(QColor(255, 255, 255, 25))
        for frac in (1.0, 0.5, 0.0):
            y = CHART_BOTTOM - int(chart_h * frac)
            p.drawLine(CHART_LEFT, y, CHART_RIGHT, y)

        for offset in range(BAR_WINDOW):
            tokens = buckets.get(slot_of(offset), 0)
            x = CHART_LEFT + offset * step
            h = max(2.0, chart_h * tokens / max_tokens)
            p.setBrush(BAR_DIM if offset > 40 else BAR_COLOR)
            p.drawRect(int(x), int(CHART_BOTTOM - h), int(bar_w), int(h))

        p.setPen(QColor(255, 255, 255, 30))
        p.drawLine(CHART_LEFT, CHART_BOTTOM + 2, CHART_RIGHT, CHART_BOTTOM + 2)

        # 横轴时间刻度：0（最新）→ 窗口上限（最旧），单位随模式（s / min）
        p.setPen(MUTED)
        fx = QFont("Segoe UI", 7)
        p.setFont(fx)
        if self._mode == "min":
            window_val, x_unit = 60, "min"
            x_ticks = [0, 15, 30, 45, 60]
        elif self._mode == "5s":
            window_val, x_unit = 300, "s"
            x_ticks = [0, 75, 150, 225, 300]
        else:
            window_val, x_unit = 60, "s"
            x_ticks = [0, 15, 30, 45, 60]
        x_label_w = 30
        for age in x_ticks:
            x = CHART_LEFT + (age / window_val) * chart_w
            x = max(CHART_LEFT, min(CHART_RIGHT - x_label_w, x))
            p.drawText(int(x), CHART_BOTTOM + 4, x_label_w, 10, Qt.AlignmentFlag.AlignLeft, f"{age}{x_unit}")
        p.drawText(CHART_RIGHT - 30, CHART_BOTTOM + 4, 30, 10, Qt.AlignmentFlag.AlignRight, f"←{window_val}{x_unit}")
