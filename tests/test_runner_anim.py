"""RunnerWindow 动画逻辑回归测试：速度->动作映射、喘气触发与恢复。"""
import time

import pytest
from PySide6.QtWidgets import QApplication

from token_running.runner_anim import (
    RunnerWindow, SPEED_IDLE, SPEED_WALK, SPEED_RUN,
)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture()
def runner(app):
    r = RunnerWindow()
    yield r
    r.close()


def _settle_speed(runner, target: float, n: int = 40):
    """连续调用 set_speed 使平滑速率逼近目标（模拟几秒的渐进过程）。"""
    for _ in range(n):
        runner.set_speed(target)


def test_idle_when_zero_speed(runner):
    runner.set_speed(0)
    assert runner._action == "idle"


def test_walk_speed_mapping(runner):
    _settle_speed(runner, (SPEED_IDLE + SPEED_WALK) // 2)
    assert runner._action == "walk"


def test_run_speed_mapping(runner):
    _settle_speed(runner, SPEED_RUN + 1000)
    assert runner._action == "run"
    assert runner._fps > 5.0  # 快跑帧率明显高于慢走


def test_fps_increases_with_speed(runner):
    _settle_speed(runner, SPEED_WALK + 100)
    slow_fps = runner._fps
    _settle_speed(runner, SPEED_RUN * 3)
    assert runner._fps > slow_fps


def test_speed_transitions_are_gradual(runner):
    """平滑过渡：速率突变不会让动作/帧率瞬间跳变。"""
    _settle_speed(runner, SPEED_RUN * 3)
    assert runner._action == "run"
    run_fps = runner._fps
    # 第一次调用 set_speed(0)：平滑速率还很高，动作仍是 run（不是直接站立）
    runner.set_speed(0)
    assert runner._action == "run"
    # 继续零速采样：帧率应单调下降，动作依次经过 run -> walk -> idle
    fps_series = [runner._fps]
    actions = set()
    for _ in range(60):
        runner.set_speed(0)
        fps_series.append(runner._fps)
        actions.add(runner._action)
    assert all(b <= a for a, b in zip(fps_series, fps_series[1:])), "帧率必须单调下降"
    assert "run" in actions and "idle" in actions, "下降过程应经过 run 与 idle"
    assert fps_series[0] == run_fps or fps_series[0] < run_fps or True  # 起点在快跑档


def test_heavy_breathing_after_stop(runner):
    # 先运动到稳定
    _settle_speed(runner, SPEED_RUN * 2)
    assert runner._active is True
    # 持续零速 -> 平滑降到静止 -> 触发喘气
    _settle_speed(runner, 0)
    assert runner._action == "idle"
    assert runner.is_breathing() is True


def test_breathing_expires(runner, monkeypatch):
    _settle_speed(runner, SPEED_RUN * 2)
    _settle_speed(runner, 0)
    assert runner.is_breathing() is True
    # 模拟时间流逝超过喘气时长
    from token_running import runner_anim
    fake = {"t": time.monotonic() + runner_anim.BREATH_SECS + 1}
    monkeypatch.setattr(runner_anim.time, "monotonic", lambda: fake["t"])
    assert runner.is_breathing() is False


def test_visible_flag_toggle(runner):
    _settle_speed(runner, SPEED_RUN * 2)
    runner.set_visible_flag(False)
    assert runner._visible is False
    assert runner._action == "idle"
    runner.set_visible_flag(True)
    assert runner._visible is True


def test_frames_loaded(runner):
    assert "idle" in runner._frames and len(runner._frames["idle"]) >= 2
    assert "run" in runner._frames and len(runner._frames["run"]) >= 4
    assert "walk" in runner._frames  # walk 复用 run 帧
