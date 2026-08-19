"""开发者模式：完整 token 使用窗 + 模拟速率滑杆 + 人物动画，用于调参测试。

与正式悬浮窗一致的全部内容（柱状图/今日/总量/费用/右键菜单），
滑杆实时模拟 token 消耗速率，同时驱动图表与小人（带平滑过渡）。

运行：
    python dev_mode.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 自包含：允许从任意目录直接运行（无需设置 PYTHONPATH）
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from token_running.realtime_ui import RealtimeWindow
from token_running.runner_anim import RunnerWindow, SPEED_IDLE, SPEED_WALK, SPEED_RUN
from token_running.source import UsageEvent

SLIDER_MAX = 8000  # 上限覆盖快跑档（>2000）

PRESETS = [
    ("站立", 0),
    ("慢走", 100),
    ("快走", 600),
    ("慢跑", 1500),
    ("快跑", 4000),
]


class SimSource:
    """模拟 token 数据源：按设定速率每秒产出事件，喂给 RealtimeWindow。"""

    def __init__(self, rate_fn) -> None:
        self._rate_fn = rate_fn
        self._daily = 0
        self._grand = 0
        self._day_key = ""

    def _bump(self, now: int) -> int:
        """按速率生成每秒 token 量（滑杆值直接作为 t/s）。"""
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if day != self._day_key:
            self._day_key = day
            self._daily = 0
        rate = max(0.0, float(self._rate_fn()))
        n = int(rate)
        if n <= 0:
            return 0
        self._daily += n
        self._grand += n
        return n

    def poll(self) -> list:
        now = int(time.time())
        n = self._bump(now)
        if n <= 0:
            return []
        return [UsageEvent(now, n, model="deepseek-v4-flash", input_t=n // 2,
                          output_t=n - n // 2, cache_read_t=0, cache_create_t=0)]

    def daily_total(self) -> int:
        return self._daily

    def total(self) -> int:
        return self._grand

    def online(self) -> bool:
        return True

    def set_windows(self, today_since=None, total_since=None) -> None:
        pass  # 模拟源无真实窗口口径


class DevModeWindow(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowTitle("Token Running · 开发者模式")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, SLIDER_MAX)
        self._slider.setValue(0)
        self._slider.setTickInterval(1000)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)

        self._sim = SimSource(lambda: self._slider.value())
        self._chart = RealtimeWindow(source=self._sim, embedded=True)
        lay.addWidget(self._chart, 0, Qt.AlignmentFlag.AlignTop)

        tip = QLabel("模拟 token 消耗速率（t/s），图表与小人均实时响应：")
        lay.addWidget(tip)

        lay.addWidget(self._slider)

        self._value_label = QLabel("0 t/s（站立）")
        self._value_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        lay.addWidget(self._value_label)

        btn_row = QHBoxLayout()
        for name, val in PRESETS:
            b = QPushButton(name)
            b.clicked.connect(lambda c=False, v=val: self._set_preset(v))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        hint = QLabel("档位参考：<1 站立 | 1-400 走 | 400-2000 慢跑 | >2000 快跑；右键图表可调 皮肤/显示方式/统计口径 等（全功能可用）")
        hint.setStyleSheet("color: #888; font-size: 12px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # 小人独立浮窗贴窗下方（与正式悬浮窗一致）
        self._runner = RunnerWindow()
        self._runner.show()

        # 100ms 轮询滑杆喂给小人（内部有平滑，拖动跟手不跳变）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    def showEvent(self, event) -> None:  # noqa: N802
        """窗口首次显示后：居中 + 同步小人位置。"""
        super().showEvent(event)
        if not getattr(self, "_centered", False):
            self._centered = True
            screen = QApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.center().x() - self.width() // 2,
                          geo.center().y() - self.height() // 2)
        self._sync_runner()

    def _set_preset(self, val: int) -> None:
        self._slider.setValue(val)
        self._tick()

    def _tick(self) -> None:
        v = self._slider.value()
        self._runner.set_speed(float(v))
        label = next((n for n, x in PRESETS if x == v), None)
        if label is None:
            if v < SPEED_IDLE:
                label = "站立"
            elif v < SPEED_WALK:
                label = "慢走"
            elif v < SPEED_RUN:
                label = "慢跑"
            else:
                label = "快跑"
        self._value_label.setText(f"{v} t/s（{label}）")
        self._sync_runner()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._sync_runner()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._runner.close()
        super().closeEvent(event)

    def _sync_runner(self) -> None:
        g = self.frameGeometry()
        self._runner.move(g.left(), g.bottom())
        self._runner.raise_()


def main() -> int:
    app = QApplication(sys.argv)
    win = DevModeWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
