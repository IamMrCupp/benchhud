"""Preferences dialog — device selection, tools, output, and logging.

Populates device dropdowns from a :class:`DeviceScanner` (so you pick a scope /
thermal cam and serial port instead of hand-editing paths), edits the launcher
tool list, and adjusts output + logging. OK writes everything back into the
config and calls the save callback. The scanner and save callback are injected,
so the whole dialog is testable offscreen with no hardware.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from benchhud.config import Config, ToolConfig
from benchhud.devices.discovery import CameraDevice, DeviceScanner, SerialDevice

SaveCallback = Callable[[Config], None]
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class PreferencesDialog(QDialog):
    def __init__(
        self,
        config: Config,
        scanner: DeviceScanner,
        *,
        on_save: SaveCallback | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._scanner = scanner
        self._on_save = on_save
        self.setWindowTitle("benchhud — Preferences")

        tabs = QTabWidget()
        tabs.addTab(self._build_devices_tab(), "Devices")
        tabs.addTab(self._build_output_tab(), "Output")
        tabs.addTab(self._build_tools_tab(), "Tools")
        tabs.addTab(self._build_logging_tab(), "Logging")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

        self.rescan_devices()

    # --- Devices ---------------------------------------------------------

    def _build_devices_tab(self) -> QWidget:
        self.scope_combo = QComboBox()
        self.thermal_combo = QComboBox()
        self.serial_combo = QComboBox()
        self.rescan_button = QPushButton("Rescan")
        self.rescan_button.clicked.connect(self.rescan_devices)

        widget = QWidget()
        form = QFormLayout(widget)
        form.addRow("Scope camera", self.scope_combo)
        form.addRow("Thermal camera", self.thermal_combo)
        form.addRow("Instrument port", self.serial_combo)
        form.addRow("", self.rescan_button)
        return widget

    def rescan_devices(self) -> None:
        """Re-enumerate devices and repopulate the dropdowns."""
        cameras = self._scanner.cameras()
        ports = self._scanner.serial_ports()
        _fill_camera_combo(self.scope_combo, cameras, self._config.panels.scope.device)
        _fill_camera_combo(self.thermal_combo, cameras, self._config.panels.thermal.device)
        _fill_serial_combo(self.serial_combo, ports, self._config.panels.telemetry.port)

    # --- Output ----------------------------------------------------------

    def _build_output_tab(self) -> QWidget:
        out = self._config.output
        self.output_enabled = QCheckBox("Enable virtual camera output")
        self.output_enabled.setChecked(out.enabled)
        self.output_width = _spin(1, 7680, out.width)
        self.output_height = _spin(1, 4320, out.height)
        self.output_fps = _spin(1, 120, out.fps)

        widget = QWidget()
        form = QFormLayout(widget)
        form.addRow(self.output_enabled)
        form.addRow("Width", self.output_width)
        form.addRow("Height", self.output_height)
        form.addRow("FPS", self.output_fps)
        return widget

    # --- Tools -----------------------------------------------------------

    def _build_tools_tab(self) -> QWidget:
        self.tools_table = QTableWidget(0, 2)
        self.tools_table.setHorizontalHeaderLabels(["Name", "Command"])
        for tool in self._config.tools:
            self._append_tool_row(tool.name, tool.command)

        self.add_tool_button = QPushButton("Add")
        self.add_tool_button.clicked.connect(lambda: self._append_tool_row("", ""))
        self.remove_tool_button = QPushButton("Remove")
        self.remove_tool_button.clicked.connect(self._remove_selected_tool)

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_tool_button)
        buttons.addWidget(self.remove_tool_button)
        buttons.addStretch(1)

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self.tools_table)
        layout.addLayout(buttons)
        return widget

    def _append_tool_row(self, name: str, command: str) -> None:
        row = self.tools_table.rowCount()
        self.tools_table.insertRow(row)
        self.tools_table.setItem(row, 0, QTableWidgetItem(name))
        self.tools_table.setItem(row, 1, QTableWidgetItem(command))

    def _remove_selected_tool(self) -> None:
        row = self.tools_table.currentRow()
        if row >= 0:
            self.tools_table.removeRow(row)

    def _collect_tools(self) -> list[ToolConfig]:
        tools = []
        for row in range(self.tools_table.rowCount()):
            name = _cell(self.tools_table, row, 0)
            command = _cell(self.tools_table, row, 1)
            if name and command:
                tools.append(ToolConfig(name=name, command=command))
        return tools

    # --- Logging ---------------------------------------------------------

    def _build_logging_tab(self) -> QWidget:
        self.logging_combo = QComboBox()
        self.logging_combo.addItems(_LOG_LEVELS)
        self.logging_combo.setCurrentText(self._config.shell.logging.level)
        widget = QWidget()
        form = QFormLayout(widget)
        form.addRow("Level", self.logging_combo)
        return widget

    # --- Apply -----------------------------------------------------------

    def accept(self) -> None:
        self.apply_to_config()
        if self._on_save is not None:
            self._on_save(self._config)
        super().accept()

    def apply_to_config(self) -> None:
        """Write the dialog's current values back into the config object."""
        # currentData() is None when a combo is empty — keep the field a string.
        self._config.panels.scope.device = self.scope_combo.currentData() or ""
        self._config.panels.thermal.device = self.thermal_combo.currentData() or ""
        self._config.panels.telemetry.port = self.serial_combo.currentData() or ""

        self._config.output.enabled = self.output_enabled.isChecked()
        self._config.output.width = self.output_width.value()
        self._config.output.height = self.output_height.value()
        self._config.output.fps = self.output_fps.value()

        self._config.shell.logging.level = self.logging_combo.currentText()
        self._config.tools = self._collect_tools()


def _fill_camera_combo(combo: QComboBox, cameras: list[CameraDevice], current: str) -> None:
    combo.clear()
    selected = -1
    for cam in cameras:
        combo.addItem(f"{cam.label} ({cam.identifier})", cam.identifier)
        if cam.identifier == current:
            selected = combo.count() - 1
    if current and selected < 0:
        combo.addItem(f"{current} (configured, not detected)", current)
        selected = combo.count() - 1
    if selected >= 0:
        combo.setCurrentIndex(selected)


def _fill_serial_combo(combo: QComboBox, ports: list[SerialDevice], current: str) -> None:
    combo.clear()
    selected = -1
    for port in ports:
        combo.addItem(f"{port.description} ({port.device})", port.device)
        if port.device == current:
            selected = combo.count() - 1
    if current and selected < 0:
        combo.addItem(f"{current} (configured, not detected)", current)
        selected = combo.count() - 1
    if selected >= 0:
        combo.setCurrentIndex(selected)


def _cell(table: QTableWidget, row: int, col: int) -> str:
    item = table.item(row, col)
    return item.text().strip() if item is not None else ""


def _spin(lo: int, hi: int, value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(lo, hi)
    spin.setValue(value)
    return spin
