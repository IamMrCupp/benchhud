"""The pinout/protocol reference panel — static crib, stream-safe."""

from __future__ import annotations

from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.pinout import PROTOCOLS, PinoutPanel


def test_pinout_panel_is_stream_safe() -> None:
    assert PinoutPanel.sensitive is False


def test_the_crib_covers_the_hardware_protocols() -> None:
    names = {name for name, _note, _signals in PROTOCOLS}
    assert {"JTAG", "SWD", "UART", "CAN", "SPI", "I²C"} <= names


def test_panel_lists_every_protocol_with_its_signals(qtbot) -> None:
    panel = PinoutPanel()
    qtbot.addWidget(panel)
    assert panel.tree.topLevelItemCount() == len(PROTOCOLS)
    # Each protocol node carries its signal rows as children.
    for row, (_name, _note, signals) in enumerate(PROTOCOLS):
        assert panel.tree.topLevelItem(row).childCount() == len(signals)


def test_shows_the_active_engagement_ref(qtbot) -> None:
    panel = PinoutPanel(active_ref=lambda: "R26-0004")
    qtbot.addWidget(panel)
    assert panel.ref_label.text() == "R26-0004"


def test_no_engagement_is_said(qtbot) -> None:
    panel = PinoutPanel(active_ref=lambda: None)
    qtbot.addWidget(panel)
    assert "no engagement" in panel.ref_label.text()


def test_window_builds_the_pinout_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window.pinout_panel, PinoutPanel)
    assert window.pinout_panel.sensitive is False
