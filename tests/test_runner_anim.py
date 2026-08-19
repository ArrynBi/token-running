"""RunnerWindow 动画逻辑回归测试：速度->动作映射、喘气触发与恢复。"""
import time

import pytest
from PySide6.QtWidgets import QApplication

from token_running.runner_anim import RunnerWindow, SPEED_IDLE, SPEED_WALK, SPEED_RUN


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture()
def runner(app):
    r = RunnerWindow()
    yield r
    r.close()


def test_idle_when_zero_speed(runner):
    runner.set_speed(0)
    assert runner._action == "idle"


def test_walk_speed_mapping(runner):
    runner.set_speed((SPEED_IDLE + SPEED_WALK) // 2)
    assert runner._action == "walk"


def test_run_speed_mapping(runner):
    runner.set_speed(SPEED_RUN + 1000)
    assert runner._action == "run"
    assert runner._fps > 5.0  # 快跑帧率明显高于慢走


def test_fps_increases_with_speed(runner):
    runner.set_speed(SPEED_WALK + 100)
    slow_fps = runner._fps
    runner.set_speed(SPEED_RUN * 3)
    assert runner._fps > slow_fps


def test_heavy_breathing_after_stop(runner):
    # 先运动
    runner.set_speed(SPEED_RUN * 2)
    assert runner._active is True
    # 骤停 -> 进入喘气
    runner.set_speed(0)
    assert runner._action == "idle"
    assert runner.is_breathing() is True


def test_breathing_expires(runner, monkeypatch):
    runner.set_speed(SPEED_RUN * 2)
    runner.set_speed(0)
    assert runner.is_breathing() is True
    # 模拟时间流逝超过喘气时长
    from token_running import runner_anim
    fake = {"t": time.monotonic() + runner_anim.BREATH_SECS + 1}
    monkeypatch.setattr(runner_anim.time, "monotonic", lambda: fake["t"])
    assert runner.is_breathing() is False


def test_visible_flag_toggle(runner):
    runner.set_speed(SPEED_RUN * 2)
    runner.set_visible_flag(False)
    assert runner._visible is False
    assert runner._action == "idle"
    runner.set_visible_flag(True)
    assert runner._visible is True


def test_frames_loaded(runner):
    assert "idle" in runner._frames and len(runner._frames["idle"]) >= 2
    assert "run" in runner._frames and len(runner._frames["run"]) >= 4
    assert "walk" in runner._frames  # walk 复用 run 帧
