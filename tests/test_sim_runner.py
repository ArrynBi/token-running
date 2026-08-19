"""Runner 模拟器回归测试：滑块值 -> 速率喂给小人 -> 动作/标签正确。"""
import pytest
from PySide6.QtWidgets import QApplication, QSlider

from sim_runner import SimulatorWindow, SLIDER_MAX, PRESETS


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture()
def sim(app):
    s = SimulatorWindow()
    yield s
    s.close()


def test_slider_range(sim):
    assert sim._slider.minimum() == 0
    assert sim._slider.maximum() == SLIDER_MAX


def test_preset_buttons_set_slider(sim):
    for name, val in PRESETS:
        sim._set_preset(val)
        assert sim._slider.value() == val


def test_speed_feeds_runner(sim):
    sim._set_preset(4000)  # 快跑
    sim._tick()
    assert sim._runner._action == "run"
    assert "快跑" in sim._value_label.text()

    sim._set_preset(0)  # 站立（平滑过渡，多次 tick 后才静止）
    for _ in range(60):
        sim._tick()
    assert sim._runner._action == "idle"
    assert "站立" in sim._value_label.text()


def test_label_for_mid_values(sim):
    sim._set_preset(100)
    sim._tick()
    assert "慢走" in sim._value_label.text()
    sim._set_preset(1500)
    sim._tick()
    assert "慢跑" in sim._value_label.text()


def test_runner_follows_simulator(sim):
    sim.move(100, 200)
    sim._sync_runner()
    g = sim.frameGeometry()
    assert sim._runner.x() == g.left()
    assert sim._runner.y() == g.bottom()
