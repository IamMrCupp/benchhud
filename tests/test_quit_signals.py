"""SIGTERM and SIGINT close the window the normal way (#291)."""

from __future__ import annotations

import os
import signal

import pytest

from benchhud.shell import quit_signals


@pytest.fixture
def restore_handlers():
    saved = {sig: signal.getsignal(sig) for sig in quit_signals.QUIT_SIGNALS}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


@pytest.mark.parametrize("sig", quit_signals.QUIT_SIGNALS, ids=["SIGTERM", "SIGINT"])
def test_a_quit_signal_closes_through_the_event_loop(qtbot, restore_handlers, sig) -> None:
    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    qtbot.addWidget(parent)
    closed: list[bool] = []
    wake = quit_signals.install(lambda: closed.append(True), parent)
    assert wake.isActive()
    os.kill(os.getpid(), sig)
    qtbot.waitUntil(lambda: closed == [True], timeout=3000)


def test_closing_the_window_turns_the_output_off(qtbot, fake_owon_serial) -> None:
    """What the signal now reaches: the close path drops the rail."""
    from benchhud.config import Config
    from benchhud.instruments.owon_spm8104 import from_serial_port
    from benchhud.instruments.session import InstrumentSession
    from benchhud.jobs.store import JobStore
    from benchhud.shell.main_window import MainWindow

    config = Config()
    config.instruments.control = True
    window = MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    driver = from_serial_port(fake_owon_serial, control_enabled=True)
    window.start_telemetry(lambda: InstrumentSession(driver, poll_interval=5.0))
    driver.output_on()
    fake_owon_serial.written.clear()
    window.close()
    assert "OUTP OFF" in fake_owon_serial.written
