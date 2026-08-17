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
