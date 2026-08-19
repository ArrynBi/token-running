"""开发者模式回归测试：SimSource 模拟数据、滑杆驱动、图表嵌入、小人跟随。"""
import time

import pytest
from PySide6.QtWidgets import QApplication

from dev_mode import DevModeWindow, SimSource, SLIDER_MAX, PRESETS
from token_running.runner_anim import SPEED_IDLE, SPEED_WALK, SPEED_RUN


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture()
def dev(app):
    w = DevModeWindow()
    yield w
    w.close()


def test_slider_range(dev):
    assert dev._slider.minimum() == 0
    assert dev._slider.maximum() == SLIDER_MAX


def test_preset_buttons_set_slider(dev):
    for name, val in PRESETS:
        dev._set_preset(val)
        assert dev._slider.value() == val


def test_speed_feeds_runner(dev):
    dev._set_preset(4000)  # 快跑
    dev._tick()
    assert dev._runner._action == "run"
    assert "快跑" in dev._value_label.text()

    dev._set_preset(0)  # 站立（平滑过渡，多次 tick 后才静止）
    for _ in range(60):
        dev._tick()
    assert dev._runner._action == "idle"
    assert "站立" in dev._value_label.text()


def test_label_for_mid_values(dev):
    dev._set_preset(100)
    dev._tick()
    assert "慢走" in dev._value_label.text()
    dev._set_preset(1500)
    dev._tick()
    assert "慢跑" in dev._value_label.text()


def test_runner_follows_window(dev):
    dev.move(100, 200)
    dev._sync_runner()
    g = dev.frameGeometry()
    assert dev._runner.x() == g.left()
    assert dev._runner.y() == g.bottom()


def test_chart_embedded_and_ticking(dev):
    """图表以嵌入模式创建（无独立窗口 flags、无自动 runner）。"""
    assert dev._chart._embedded is True
    assert dev._chart._runner is None
    assert dev._chart.parent() is dev or dev._chart.parent() is not None


def test_sim_source_generates_events(dev):
    dev._set_preset(1000)
    evs = dev._sim.poll()
    assert len(evs) == 1
    assert evs[0].tokens == 1000
    assert dev._sim.daily_total() == 1000
    assert dev._sim.total() == 1000
    # 零速率不产出
    dev._set_preset(0)
    assert dev._sim.poll() == []


def test_chart_receives_sim_events(dev):
    """滑杆速率应进入图表分桶（模拟数据驱动柱状图）。"""
    dev._set_preset(500)
    dev._chart._tick()  # 触发一次主循环 tick
    total_bars = sum(sum(b.values()) for b in dev._chart._bars_sec.values())
    assert total_bars > 0, "模拟速率应写入图表分桶"
