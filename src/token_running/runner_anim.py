"""人物跑步动画浮窗：随 token 消耗速度呈现 站立/慢走/快走/慢跑/快跑/喘气。

素材：OpenGameArt CC0 "Runner character"（idle 5 帧 / run 8 帧 / jump 4 帧，32x32），
已裁剪为独立 PNG 存于 assets/runner/{idle,run,jump}/N.png。

设计：
- 速度 -> 动作：tokens/秒 越高动作越快；零速进入站立，跑久骤停进入喘气
- 快慢通过播放帧率（FPS）体现：walk 低帧率慢走、高帧率快走；run 同理
- 喘气：idle 帧 + 程序化呼吸位移（上下起伏），数秒后恢复站立
- 独立置顶无边框透明窗口，由主窗 moveEvent 驱动跟随（贴左下，与主窗左对齐）
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtWidgets import QWidget

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "runner"

# 动作 -> (帧目录, 帧数)；walk 无单独素材时用 run 帧降帧率模拟
ACTIONS = {"idle": ("idle", 5), "run": ("run", 8), "jump": ("jump", 4)}

CHAR_H = 96      # 窗口高度（人物 32x32 放大 3 倍）
CHAR_W = 96
FPS_MIN = 2.0   # 最低帧率（站立/慢走）
FPS_MAX = 24.0  # 最高帧率（快跑）

# token/秒 档位阈值（可通过 set_speed 传入实际速率自动缩放）
SPEED_IDLE = 1        # < 1 t/s 视为静止
SPEED_WALK = 400      # < 400 t/s 走
SPEED_RUN = 2000      # < 2000 t/s 慢跑，>= 快跑

# 喘气参数
BREATH_SECS = 6.0      # 骤停后喘气持续时长
BREATH_AMPL = 6        # 喘气呼吸位移（像素）
BREATH_RATE = 3.0      # 喘气呼吸频率（Hz）
IDLE_BREATH_AMPL = 2   # 站立呼吸位移


class RunnerWindow(QWidget):
    """随 token 速率播放人物动画的独立浮窗。"""

    def __init__(self) -> None:
        super().__init__(None)
        self._frames: dict[str, list[QPixmap]] = {}
        self._load_frames()
        self._action = "idle"
        self._frame = 0
        self._fps = 3.0
        self._phase = 0.0          # 帧内相位（用于换帧节奏）
        self._breath_until = 0.0   # 喘气结束时刻
        self._last_speed = 0.0
        self._active = False
        self._visible = True
        self.setWindowTitle("Runner")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput,  # 不挡鼠标
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFixedSize(CHAR_W, CHAR_H)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(50)  # 20Hz 调度，按 _fps 决定是否换帧

    def _load_frames(self) -> None:
        for name, (sub, n) in ACTIONS.items():
            d = ASSETS_DIR / sub
            frames = []
            for i in range(n):
                p = d / f"{i}.png"
                if p.exists():
                    pm = QPixmap(str(p))
                    if not pm.isNull():
                        frames.append(pm.scaled(CHAR_W, CHAR_H, Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.FastTransformation))
            if frames:
                self._frames[name] = frames
        # walk 用 run 帧降帧率模拟
        if "run" in self._frames and "walk" not in self._frames:
            self._frames["walk"] = self._frames["run"]
        # 素材缺失时退化为 1 帧空动作
        if not self._frames.get("idle"):
            self._frames["idle"] = [QPixmap(CHAR_W, CHAR_H)]

    # ---- 外部接口 ----

    def set_speed(self, tokens_per_sec: float) -> None:
        """每秒由主窗调用：传入当前 token 速率，内部决策动作与帧率。"""
        now = time.monotonic()
        self._last_speed = tokens_per_sec
        was_active = self._active
        self._active = tokens_per_sec >= SPEED_IDLE
        if was_active and not self._active:
            # 从运动骤停：进入喘气
            self._breath_until = now + BREATH_SECS
        if not self._visible:
            self._action = "idle"
            return
        if tokens_per_sec >= SPEED_RUN:
            self._action = "run"
            self._fps = FPS_MIN + (FPS_MAX - FPS_MIN) * min(1.0, (tokens_per_sec - SPEED_RUN) / 3000.0)
        elif tokens_per_sec >= SPEED_WALK:
            self._action = "walk"
            self._fps = FPS_MIN + (FPS_MAX - FPS_MIN) * (tokens_per_sec - SPEED_IDLE) / (SPEED_RUN - SPEED_IDLE)
        elif tokens_per_sec >= SPEED_IDLE:
            self._action = "walk"
            self._fps = FPS_MIN + 2.0  # 极低速仍走（慢走）
        else:
            self._action = "idle"
            self._fps = 3.0 if now >= self._breath_until else BREATH_RATE * 2.0
        self.update()

    def is_breathing(self) -> bool:
        return time.monotonic() < self._breath_until

    def set_visible_flag(self, v: bool) -> None:
        """菜单开关：隐藏时不播放（保留状态）。"""
        self._visible = v
        self.setVisible(v and self._active)
        if not v:
            self._action = "idle"
        self.update()

    # ---- 动画推进 ----

    def _advance(self) -> None:
        if not self._visible or not self._active or not self._frames.get(self._action):
            self.hide()
            return
        self.show()
        now = time.monotonic()
        interval = 1.0 / max(self._fps, 0.1)
        self._phase += 0.05
        if self._phase >= interval:
            self._phase = 0.0
            frames = self._frames.get(self._action, self._frames["idle"])
            self._frame = (self._frame + 1) % len(frames)
        if now >= self._breath_until and self._action == "idle":
            self._frame = 0
        self.update()

    # ---- 绘制 ----

    def paintEvent(self, _event) -> None:  # noqa: N802
        frames = self._frames.get(self._action) or self._frames["idle"]
        if not frames:
            return
        idx = min(self._frame, len(frames) - 1)
        pm = frames[idx]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        now = time.monotonic()
        if self._action == "idle":
            amp = BREATH_AMPL if now < self._breath_until else IDLE_BREATH_AMPL
            rate = BREATH_RATE if now < self._breath_until else 0.8
            dy = int(amp * (0.5 - 0.5 * math.cos(2 * math.pi * rate * now)))
            p.drawPixmap(0, CHAR_H - pm.height() + dy, pm)
        else:
            p.drawPixmap(0, CHAR_H - pm.height(), pm)
        p.end()
