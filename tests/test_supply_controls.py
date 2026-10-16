"""Supply controls (#164): what the Meter panel may send, and what reaches the wire.

The window tests run a real InstrumentSession on the fake OWON serial port, so
"confirm on enable", "output off always works", "the ceiling is the driver's"
and "never retried" are checked against the lines actually written.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox

from benchhud.config import Config
from benchhud.instruments.owon_spm8104 import from_serial_port
from benchhud.instruments.session import InstrumentSession
from benchhud.jobs.store import JobStore
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels import supply_controls
from benchhud.shell.panels.supply_controls import SupplyControls

# --- the widget ------------------------------------------------------------------------


def _controls(qtbot, confirm=lambda *_: True) -> SupplyControls:
    w = SupplyControls(confirm=confirm)
    qtbot.addWidget(w)
    return w


def test_read_only_means_only_output_off(qtbot) -> None:
    w = _controls(qtbot)
    w.set_available(connected=True, control=False, max_voltage=5.5, max_current=1.0)
    assert w.off_button.isEnabled()
    for widget in (w.on_button, w.set_voltage_button, w.set_current_button):
        assert not widget.isEnabled()
    assert "Read-only" in w.status.text()


def test_nothing_works_without_an_instrument(qtbot) -> None:
    w = _controls(qtbot)
    w.set_available(connected=False, control=True, max_voltage=5.5, max_current=1.0)
    assert not w.off_button.isEnabled() and not w.on_button.isEnabled()


def test_the_spin_boxes_stop_at_the_ceilings(qtbot) -> None:
    w = _controls(qtbot)
    w.set_available(connected=True, control=True, max_voltage=3.3, max_current=0.5)
    w.voltage.setValue(12.0)
    w.current.setValue(2.0)
    assert (w.voltage.value(), w.current.value()) == (3.3, 0.5)


def test_output_on_needs_a_yes(qtbot) -> None:
    asked: list[tuple[float, float]] = []

    def say_no(_parent, volts, amps):
        asked.append((volts, amps))
        return False

    w = _controls(qtbot, confirm=say_no)
    w.set_available(connected=True, control=True, max_voltage=5.5, max_current=1.0)
    w.voltage.setValue(3.3)
    w.current.setValue(0.25)
    fired: list[bool] = []
    w.output_on_requested.connect(lambda: fired.append(True))
    w.on_button.click()
    assert asked == [(3.3, 0.25)]  # the prompt names the setpoints
    assert fired == [] and "left off" in w.status.text()


def test_the_prompt_defaults_to_no(monkeypatch, qtbot) -> None:
    """Enter at the prompt must not energize a rail."""
    seen = {}

    def question(parent, title, text, buttons, default):
        seen.update(text=text, default=default)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    parent = _controls(qtbot)
    assert supply_controls.confirm_output_on(parent, 3.3, 0.25) is False
    assert seen["default"] == QMessageBox.StandardButton.No
    assert "3.30 V" in seen["text"] and "0.250 A" in seen["text"]


# --- through the window, to the wire ---------------------------------------------------------


@pytest.fixture
def window_with(qtbot, fake_owon_serial):
    """A window with a live session on the fake OWON, control on or off."""
    windows = []

    def make(control: bool) -> tuple[MainWindow, list[str]]:
        config = Config()
        config.instruments.control = control
        window = MainWindow(config, job_store=JobStore(":memory:"))
        qtbot.addWidget(window)
        driver = from_serial_port(fake_owon_serial, control_enabled=control)
        window.start_telemetry(lambda: InstrumentSession(driver, poll_interval=5.0))
        windows.append(window)
        return window, fake_owon_serial.written

    yield make
    for window in windows:
        window.stop_telemetry()


def _commands(written: list[str]) -> list[str]:
    """The writes, minus the session's own polls and its OUTP OFF on connect."""
    return [w for w in written[1:] if not w.endswith("?")]


def test_a_setpoint_reaches_the_supply(qtbot, window_with) -> None:
    window, written = window_with(control=True)
    supply = window.power_panel.supply
    supply.voltage.setValue(3.3)
    supply.set_voltage_button.click()
    qtbot.waitUntil(lambda: "done" in supply.status.text(), timeout=5000)
    assert _commands(written) == ["VOLT 3.300"]


def test_output_on_after_yes_reaches_the_supply(qtbot, window_with) -> None:
    window, written = window_with(control=True)
    supply = window.power_panel.supply
    supply._confirm = lambda *_: True
    supply.on_button.click()
    qtbot.waitUntil(lambda: "Output ON: done" in supply.status.text(), timeout=5000)
    assert _commands(written) == ["OUTP ON"]


def test_over_the_ceiling_is_refused_by_the_driver(qtbot, window_with) -> None:
    """The spin box stops at the ceiling; the driver refuses regardless."""
    window, written = window_with(control=True)
    window.run_supply_command("Set 12 V", lambda d: d.set_voltage(12.0))
    supply = window.power_panel.supply
    qtbot.waitUntil(lambda: "not done" in supply.status.text(), timeout=5000)
    assert "ceiling" in supply.status.text()
    assert _commands(written) == []


def test_with_control_off_output_on_is_refused_but_off_works(qtbot, window_with) -> None:
    window, written = window_with(control=False)
    supply = window.power_panel.supply
    window.run_supply_command("Output ON", lambda d: d.output_on())
    qtbot.waitUntil(lambda: "not done" in supply.status.text(), timeout=5000)
    supply.off_button.click()
    qtbot.waitUntil(lambda: "Output OFF: done" in supply.status.text(), timeout=5000)
    assert _commands(written) == ["OUTP OFF"]


def test_a_failed_command_is_shown_and_never_retried(qtbot, window_with) -> None:
    window, _written = window_with(control=True)
    calls = []

    def flaky(driver):
        calls.append(1)
        raise TimeoutError("no answer from the supply")

    window.run_supply_command("Output ON", flaky)
    supply = window.power_panel.supply
    qtbot.waitUntil(lambda: "not done" in supply.status.text(), timeout=5000)
    qtbot.wait(300)  # long enough for a retry to have happened, if there were one
    assert calls == [1]
    assert "no answer" in supply.status.text()


def test_no_instrument_means_nothing_is_sent(qtbot) -> None:
    window = MainWindow(Config(), job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    window.run_supply_command("Output ON", lambda d: d.output_on())
    assert "no instrument is connected" in window.power_panel.supply.status.text()
