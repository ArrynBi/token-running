"""Runner 动画模拟器：滑杆实时调整 token 消耗速度，驱动小人动画，便于调参测试。

运行：
    python sim_runner.py            （或在 src 同级）
    python -m PySide6 已包含于 requirements

滑杆范围 0 ~ 8000 t/s，覆盖 站立/慢走/快走/慢跑/快跑 全档位；
小人浮窗贴在模拟器窗口下方，拖动模拟器跟随移动（与真实悬浮窗一致）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 自包含：允许从任意目录直接运行（无需设置 PYTHONPATH）
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from token_running.runner_anim import RunnerWindow, SPEED_IDLE, SPEED_WALK, SPEED_RUN

SLIDER_MAX = 8000  # 上限覆盖快跑档（>2000）

PRESETS = [
    ("站立", 0),
    ("慢走", 100),
    ("快走", 600),
    ("慢跑", 1500),
    ("快跑", 4000),
]


class SimulatorWindow(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowTitle("Runner 模拟器")
        self.setFixedWidth(420)

        self._runner = RunnerWindow()
        self._runner.show()

        lay = QVBoxLayout(self)
        tip = QLabel("拖动滑杆调整 token 消耗速度（t/s），小人实时响应：")
        lay.addWidget(tip)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, SLIDER_MAX)
        self._slider.setValue(0)
        self._slider.setTickInterval(1000)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
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

        hint = QLabel("档位参考：<1 站立 | 1-400 走 | 400-2000 慢跑 | >2000 快跑\n拖动模拟器窗口，小人会贴在下方跟随")
        hint.setStyleSheet("color: #888; font-size: 12px;")
        lay.addWidget(hint)

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
    win = SimulatorWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
