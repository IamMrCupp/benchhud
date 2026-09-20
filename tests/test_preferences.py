"""Tests for the preferences dialog + menu wiring (hardware-free)."""

from __future__ import annotations

from benchhud.config import Config, ToolConfig
from benchhud.devices.discovery import CameraDevice, SerialDevice
from benchhud.shell.main_window import MainWindow
from benchhud.shell.preferences import PreferencesDialog


class _FakeScanner:
    def __init__(self, cameras, ports) -> None:
        self._cameras = cameras
        self._ports = ports

    def cameras(self):
        return list(self._cameras)

    def serial_ports(self):
        return list(self._ports)


def _scanner() -> _FakeScanner:
    return _FakeScanner(
        cameras=[
            CameraDevice("/dev/video4", "YW3609A", 1),
            CameraDevice("/dev/video6", "Thermal Camera (UVC)", 2),
        ],
        ports=[SerialDevice("/dev/ttyUSB0", "CH340 serial")],
    )


def test_populates_device_dropdowns(qtbot) -> None:
    dialog = PreferencesDialog(Config(), _scanner())
    qtbot.addWidget(dialog)
    assert dialog.scope_combo.count() == 2
    assert dialog.serial_combo.count() == 1
    # userData carries the identifier the capture layer opens.
    assert dialog.scope_combo.itemData(0) == "/dev/video4"


def test_preselects_configured_device(qtbot) -> None:
    config = Config()
    config.panels.scope.device = "/dev/video4"
    config.panels.thermal.device = "/dev/video6"
    dialog = PreferencesDialog(config, _scanner())
    qtbot.addWidget(dialog)
    assert dialog.scope_combo.currentData() == "/dev/video4"
    assert dialog.thermal_combo.currentData() == "/dev/video6"


def test_configured_but_undetected_device_preserved(qtbot) -> None:
    config = Config()
    config.panels.scope.device = "/dev/video99"  # not in the scan
    dialog = PreferencesDialog(config, _scanner())
    qtbot.addWidget(dialog)
    assert dialog.scope_combo.currentData() == "/dev/video99"
    assert "not detected" in dialog.scope_combo.currentText()


def test_rescan_repopulates(qtbot) -> None:
    # Empty device fields so there's no "configured but not detected" fallback entry.
    config = Config()
    config.panels.scope.device = ""
    config.panels.thermal.device = ""
    config.panels.telemetry.port = ""
    scanner = _FakeScanner(cameras=[], ports=[])
    dialog = PreferencesDialog(config, scanner)
    qtbot.addWidget(dialog)
    assert dialog.scope_combo.count() == 0
    scanner._cameras = [CameraDevice("/dev/video0", "cam", 0)]
    dialog.rescan_devices()
    assert dialog.scope_combo.count() == 1


def test_tools_table_reflects_config(qtbot) -> None:
    config = Config()
    config.tools = [ToolConfig("OpenBoardView", "openboardview")]
    dialog = PreferencesDialog(config, _scanner())
    qtbot.addWidget(dialog)
    assert dialog.tools_table.rowCount() == 1
    assert dialog.tools_table.item(0, 0).text() == "OpenBoardView"


def test_apply_writes_selections_to_config(qtbot) -> None:
    config = Config()
    dialog = PreferencesDialog(config, _scanner())
    qtbot.addWidget(dialog)
    dialog.scope_combo.setCurrentIndex(0)  # /dev/video4
    dialog.thermal_combo.setCurrentIndex(1)  # /dev/video6
    dialog.serial_combo.setCurrentIndex(0)  # /dev/ttyUSB0
    dialog.output_fps.setValue(24)
    dialog.logging_combo.setCurrentText("DEBUG")
    dialog._append_tool_row("minipro-gui", "minipro-gui")

    dialog.apply_to_config()

    assert config.panels.scope.device == "/dev/video4"
    assert config.panels.thermal.device == "/dev/video6"
    assert config.panels.telemetry.port == "/dev/ttyUSB0"
    assert config.output.fps == 24
    assert config.shell.logging.level == "DEBUG"
    assert config.tools == [ToolConfig("minipro-gui", "minipro-gui")]


def test_faults_tab_populates_and_applies(qtbot) -> None:
    config = Config()
    config.faults.auto_log = True
    config.faults.current_threshold_a = 2.0
    dialog = PreferencesDialog(config, _scanner())
    qtbot.addWidget(dialog)
    # populated from config
    assert dialog.fault_auto_log.isChecked() is True
    assert dialog.fault_threshold.value() == 2.0

    dialog.fault_auto_log.setChecked(False)
    dialog.fault_threshold.setValue(3.5)
    dialog.apply_to_config()

    assert config.faults.auto_log is False
    assert config.faults.current_threshold_a == 3.5


def test_accept_calls_on_save(qtbot) -> None:
    saved: list[Config] = []
    config = Config()
    dialog = PreferencesDialog(config, _scanner(), on_save=saved.append)
    qtbot.addWidget(dialog)
    dialog.accept()
    assert saved == [config]


def test_remove_tool_row(qtbot) -> None:
    config = Config()
    config.tools = [ToolConfig("a", "a"), ToolConfig("b", "b")]
    dialog = PreferencesDialog(config, _scanner())
    qtbot.addWidget(dialog)
    dialog.tools_table.setCurrentCell(0, 0)
    dialog._remove_selected_tool()
    dialog.apply_to_config()
    assert [t.name for t in config.tools] == ["b"]


def test_main_window_has_preferences_action(qtbot) -> None:
    window = MainWindow(scanner=_scanner())
    qtbot.addWidget(window)
    assert window.preferences_action.text() == "Preferences…"


def test_open_preferences_returns_dialog(qtbot) -> None:
    window = MainWindow(scanner=_scanner())
    qtbot.addWidget(window)
    dialog = window.open_preferences()
    assert isinstance(dialog, PreferencesDialog)
    assert dialog.scope_combo.count() == 2
    dialog.close()


def test_inventory_tab_round_trips_to_config(qtbot) -> None:
    config = Config()
    dialog = PreferencesDialog(config, _scanner())
    qtbot.addWidget(dialog)
    dialog.inventory_base_url.setText("  http://parts:8000  ")
    dialog.inventory_token.setText("tok")

    dialog.apply_to_config()

    assert config.inventory.base_url == "http://parts:8000"
    assert config.inventory.token == "tok"


def test_inventory_token_field_is_masked(qtbot) -> None:
    dialog = PreferencesDialog(Config(), _scanner())
    qtbot.addWidget(dialog)
    from PySide6.QtWidgets import QLineEdit

    assert dialog.inventory_token.echoMode() == QLineEdit.EchoMode.Password
