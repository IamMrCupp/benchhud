"""Preferences dialog — device selection, tools, output, and logging.

Populates device dropdowns from a :class:`DeviceScanner` (so you pick a scope /
thermal cam and serial port instead of hand-editing paths), edits the launcher
tool list, and adjusts output + logging. OK writes everything back into the
config and calls the save callback. The scanner and save callback are injected,
so the whole dialog is testable offscreen with no hardware.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from benchhud.boards.library import DEFAULT_EXTENSIONS
from benchhud.config import Config, ToolConfig
from benchhud.devices.discovery import CameraDevice, DeviceScanner, SerialDevice
from benchhud.launcher.catalog import is_command_line
from benchhud.shell.qt_util import fit_combo_popup, grow_fields
from benchhud.shell.tool_picker import ToolPickerDialog
from benchhud.terminal import SUPPORTED as TERMINAL_SUPPORTED
from benchhud.terminal import login_shell

SaveCallback = Callable[[Config], None]
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_OVERLAY_CORNERS = (
    ("Bottom left", "bottom-left"),
    ("Bottom right", "bottom-right"),
    ("Top left", "top-left"),
    ("Top right", "top-right"),
)
_BOARD_VIEWERS = (
    ("Auto (any installed)", "auto"),
    ("OpenBoardView", "openboardview"),
    ("FlexBV", "flexbv"),
)


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
        tabs.addTab(self._build_faults_tab(), "Faults")
        tabs.addTab(self._build_terminal_tab(), "Terminal")
        tabs.addTab(self._build_tickets_tab(), "Tickets")
        tabs.addTab(self._build_inventory_tab(), "Inventory")
        tabs.addTab(self._build_boards_tab(), "Boards")
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
        grow_fields(form)
        form.addRow("Scope camera", self.scope_combo)
        form.addRow("Thermal camera", self.thermal_combo)
        form.addRow("Instrument port", self.serial_combo)
        rescan_row = QHBoxLayout()
        rescan_row.addWidget(self.rescan_button)
        rescan_row.addStretch(1)
        form.addRow("", rescan_row)
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
        self.output_device = QLineEdit(out.device)
        self.output_device.setPlaceholderText("/dev/video10")
        self.output_width = _spin(1, 7680, out.width)
        self.output_height = _spin(1, 4320, out.height)
        self.output_fps = _spin(1, 120, out.fps)

        overlay = self._config.telemetry_overlay
        self.overlay_enabled = QCheckBox("Overlay live meter readings on the output")
        self.overlay_enabled.setChecked(overlay.enabled)
        self.overlay_enabled.setToolTip("Burn the power/DMM readout into the streamed frame")
        self.overlay_corner = QComboBox()
        for label, data in _OVERLAY_CORNERS:
            self.overlay_corner.addItem(label, data)
        corner_idx = self.overlay_corner.findData(overlay.corner)
        if corner_idx >= 0:
            self.overlay_corner.setCurrentIndex(corner_idx)
        fit_combo_popup(self.overlay_corner)

        self.overlay_show_psu = QCheckBox("PSU (voltage / current / power / max)")
        self.overlay_show_psu.setChecked(overlay.show_psu)
        self.overlay_show_dmm = QCheckBox("DMM reading")
        self.overlay_show_dmm.setChecked(overlay.show_dmm)
        self.overlay_scale = _ratio_spin(0.3, 4.0, overlay.scale)
        self.overlay_scale.setToolTip("Size multiplier on top of the automatic frame scaling")
        self.overlay_opacity = _ratio_spin(0.1, 1.0, overlay.opacity)
        self.overlay_opacity.setToolTip("Below 1.0 lets the board show through the readout")

        graph = self._config.graph_overlay
        self.graph_enabled = QCheckBox("Overlay the live power graph (V / A / W) on the output")
        self.graph_enabled.setChecked(graph.enabled)
        self.graph_enabled.setToolTip("Burn the meter's trend chart into the streamed frame")
        self.graph_corner = QComboBox()
        for label, data in _OVERLAY_CORNERS:
            self.graph_corner.addItem(label, data)
        graph_idx = self.graph_corner.findData(graph.corner)
        if graph_idx >= 0:
            self.graph_corner.setCurrentIndex(graph_idx)
        fit_combo_popup(self.graph_corner)
        self.graph_window = _spin(10, 600, round(graph.window_seconds))
        self.graph_window.setSuffix(" s")
        self.graph_window.setToolTip("How much history the on-stream graph shows")
        self.graph_scale = _ratio_spin(0.3, 4.0, graph.scale)
        self.graph_opacity = _ratio_spin(0.1, 1.0, graph.opacity)

        sources = self._config.browser_sources
        self.browser_sources_enabled = QCheckBox("Serve panels to OBS as Browser sources")
        self.browser_sources_enabled.setChecked(sources.enabled)
        self.browser_sources_enabled.setToolTip(
            "One URL per panel (meter, graph, job) on 127.0.0.1 — "
            "add each to OBS as a Browser source"
        )
        self.browser_sources_port = _spin(1024, 65535, sources.port)

        widget = QWidget()
        form = QFormLayout(widget)
        grow_fields(form)
        form.addRow(self.output_enabled)
        form.addRow("Loopback device", self.output_device)
        form.addRow("Width", self.output_width)
        form.addRow("Height", self.output_height)
        form.addRow("FPS", self.output_fps)
        form.addRow(self.overlay_enabled)
        form.addRow("Overlay position", self.overlay_corner)
        form.addRow("Overlay shows", self.overlay_show_psu)
        form.addRow("", self.overlay_show_dmm)
        form.addRow("Overlay size", self.overlay_scale)
        form.addRow("Overlay opacity", self.overlay_opacity)
        form.addRow(self.graph_enabled)
        form.addRow("Graph position", self.graph_corner)
        form.addRow("Graph history", self.graph_window)
        form.addRow("Graph size", self.graph_scale)
        form.addRow("Graph opacity", self.graph_opacity)
        form.addRow(self.browser_sources_enabled)
        form.addRow("Browser source port", self.browser_sources_port)
        return widget

    # --- Tools -----------------------------------------------------------

    def _build_tools_tab(self) -> QWidget:
        self.tools_table = QTableWidget(0, 3)
        self.tools_table.setHorizontalHeaderLabels(["Name", "Command", "Terminal"])
        self.tools_table.horizontalHeaderItem(2).setToolTip(
            "A command-line tool: open it in the Terminal panel instead of as a window"
        )
        header = self.tools_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tools_table.verticalHeader().setVisible(False)
        for tool in self._config.tools:
            # Ticked for a known command-line tool even when its entry predates
            # the flag, matching what the Tools pane does with it (#230).
            terminal = tool.terminal or is_command_line(tool.command)
            self._append_tool_row(tool.name, tool.command, terminal)

        self.add_tool_button = QPushButton("Add")
        self.add_tool_button.clicked.connect(lambda: self._append_tool_row("", "", False))
        self.remove_tool_button = QPushButton("Remove")
        self.remove_tool_button.clicked.connect(self._remove_selected_tool)
        self.detect_tools_button = QPushButton("Detect installed…")
        self.detect_tools_button.setToolTip("Scan this system for known bench tools")
        self.detect_tools_button.clicked.connect(self.open_tool_picker)
        self.browse_tool_button = QPushButton("Browse…")
        self.browse_tool_button.setToolTip("Pick an executable from the filesystem")
        self.browse_tool_button.clicked.connect(self.browse_for_tool)

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_tool_button)
        buttons.addWidget(self.remove_tool_button)
        buttons.addStretch(1)
        buttons.addWidget(self.detect_tools_button)
        buttons.addWidget(self.browse_tool_button)

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self.tools_table)
        layout.addLayout(buttons)
        return widget

    def _append_tool_row(self, name: str, command: str, terminal: bool = False) -> None:
        row = self.tools_table.rowCount()
        self.tools_table.insertRow(row)
        self.tools_table.setItem(row, 0, QTableWidgetItem(name))
        self.tools_table.setItem(row, 1, QTableWidgetItem(command))
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        check.setCheckState(Qt.CheckState.Checked if terminal else Qt.CheckState.Unchecked)
        self.tools_table.setItem(row, 2, check)

    def open_tool_picker(self) -> ToolPickerDialog:
        """Scan for installed tools; add the ticked ones (returned for tests)."""
        dialog = ToolPickerDialog(parent=self)
        dialog.accepted.connect(lambda: self.add_tools(dialog.selected_tools()))
        dialog.open()
        return dialog

    def browse_for_tool(self) -> None:
        """Pick an executable from the filesystem and add it as a tool."""
        path, _ = QFileDialog.getOpenFileName(self, "Select a tool executable")
        if path:
            self.add_tools([ToolConfig(name=Path(path).stem, command=path)])

    def add_tools(self, tools: list[ToolConfig]) -> None:
        """Append tools, skipping ones already in the table (by command)."""
        existing = {_cell(self.tools_table, row, 1) for row in range(self.tools_table.rowCount())}
        for tool in tools:
            if tool.command in existing:
                continue
            self._append_tool_row(tool.name, tool.command, tool.terminal)
            existing.add(tool.command)

    def _remove_selected_tool(self) -> None:
        row = self.tools_table.currentRow()
        if row >= 0:
            self.tools_table.removeRow(row)

    def _collect_tools(self) -> list[ToolConfig]:
        tools = []
        for row in range(self.tools_table.rowCount()):
            name = _cell(self.tools_table, row, 0)
            command = _cell(self.tools_table, row, 1)
            check = self.tools_table.item(row, 2)
            terminal = check is not None and check.checkState() == Qt.CheckState.Checked
            if name and command:
                tools.append(ToolConfig(name=name, command=command, terminal=terminal))
        return tools

    # --- Logging ---------------------------------------------------------

    def _build_faults_tab(self) -> QWidget:
        faults = self._config.faults
        self.fault_auto_log = QCheckBox("Auto-log over-current faults to the selected job")
        self.fault_auto_log.setChecked(faults.auto_log)
        self.fault_threshold = QDoubleSpinBox()
        self.fault_threshold.setRange(0.0, 100.0)
        self.fault_threshold.setDecimals(2)
        self.fault_threshold.setSingleStep(0.1)
        self.fault_threshold.setSuffix(" A")
        self.fault_threshold.setValue(faults.current_threshold_a)
        self.fault_threshold.setToolTip("A note is logged when the current first crosses this")

        widget = QWidget()
        form = QFormLayout(widget)
        grow_fields(form)
        form.addRow(self.fault_auto_log)
        form.addRow("Current threshold", self.fault_threshold)
        return widget

    def _build_terminal_tab(self) -> QWidget:
        term = self._config.terminal
        # Shown and applied on OK; generated now so the link can be copied into
        # OBS before the first launch with sharing on.
        self._share_token = term.share_token or secrets.token_urlsafe(18)
        self.terminal_enabled = QCheckBox("Show a terminal panel (your shell, inside benchhud)")
        self.terminal_enabled.setChecked(term.enabled)
        self.terminal_shell = QLineEdit(term.shell)
        self.terminal_shell.setPlaceholderText(f"{login_shell()[0]} (your login shell)")
        self.terminal_share = QCheckBox("Also show it on stream, read-only (OBS Browser source)")
        self.terminal_share.setChecked(term.share)
        self.terminal_share.setToolTip(
            "Everything that scrolls past goes on air. Use the panel's Pause stream "
            "button before showing anything private."
        )
        self.terminal_share_url = QLineEdit()
        self.terminal_share_url.setReadOnly(True)
        self.terminal_share_url.setToolTip("Add this to OBS as a Browser source")
        self.browser_sources_port.valueChanged.connect(self._refresh_share_url)
        new_link = QPushButton("New link")
        new_link.setToolTip("Make a new stream link; the old one stops working")
        new_link.clicked.connect(self.regenerate_share_token)
        link_row = QHBoxLayout()
        link_row.addWidget(self.terminal_share_url, stretch=1)
        link_row.addWidget(new_link)
        self._refresh_share_url()

        widget = QWidget()
        form = QFormLayout(widget)
        grow_fields(form)
        if not TERMINAL_SUPPORTED:
            form.addRow(QLabel("The terminal isn't available on Windows yet."))
            self.terminal_enabled.setEnabled(False)
        form.addRow(self.terminal_enabled)
        form.addRow("Shell", self.terminal_shell)
        form.addRow(self.terminal_share)
        form.addRow("Stream link", link_row)
        note = QLabel(
            "The stream view only shows output: nobody can type through it. It runs "
            "on the browser-source port, loopback only."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return widget

    def regenerate_share_token(self) -> None:
        """New stream link; the old one stops working once OK is pressed."""
        self._share_token = secrets.token_urlsafe(18)
        self._refresh_share_url()

    def _refresh_share_url(self) -> None:
        port = self.browser_sources_port.value()
        self.terminal_share_url.setText(
            f"http://127.0.0.1:{port}/terminal?token={self._share_token}"
        )
        self.terminal_share_url.setCursorPosition(0)  # show where it starts

    def _build_tickets_tab(self) -> QWidget:
        intake = self._config.intake
        self.intake_base_url = QLineEdit(intake.base_url)
        self.intake_base_url.setPlaceholderText("http://intake.repair-bench:8000")
        self.intake_token = QLineEdit(intake.token)
        self.intake_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.intake_token.setPlaceholderText("intake API token")

        widget = QWidget()
        form = QFormLayout(widget)
        grow_fields(form)
        form.addRow("Intake base URL", self.intake_base_url)
        form.addRow("API token", self.intake_token)
        return widget

    def _build_inventory_tab(self) -> QWidget:
        inventory = self._config.inventory
        self.inventory_base_url = QLineEdit(inventory.base_url)
        self.inventory_base_url.setPlaceholderText("http://192.168.7.204:8000")
        self.inventory_token = QLineEdit(inventory.token)
        self.inventory_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.inventory_token.setPlaceholderText("bench-parts API token")

        widget = QWidget()
        form = QFormLayout(widget)
        grow_fields(form)
        form.addRow("bench-parts base URL", self.inventory_base_url)
        form.addRow("API token", self.inventory_token)
        return widget

    def _build_boards_tab(self) -> QWidget:
        boards = self._config.boards
        self.boards_library_dir = QLineEdit(boards.library_dir)
        self.boards_library_dir.setPlaceholderText("~/boards")
        self.boards_browse_button = QPushButton("Browse…")
        self.boards_browse_button.clicked.connect(self.browse_for_board_library)
        self.boards_extensions = QLineEdit(", ".join(boards.extensions))
        self.boards_extensions.setToolTip("Comma-separated board-view formats")
        self.boards_schematic_extensions = QLineEdit(", ".join(boards.schematic_extensions))
        self.boards_schematic_extensions.setToolTip(
            "Schematic formats filed next to the board (same name)"
        )
        self.boards_viewer = QComboBox()
        for label, data in _BOARD_VIEWERS:
            self.boards_viewer.addItem(label, data)
        viewer_idx = self.boards_viewer.findData(boards.viewer)
        if viewer_idx >= 0:
            self.boards_viewer.setCurrentIndex(viewer_idx)
        self.boards_viewer.setToolTip("Which viewer 'Open board' launches")
        fit_combo_popup(self.boards_viewer)

        row = QHBoxLayout()
        row.addWidget(self.boards_library_dir, stretch=1)
        row.addWidget(self.boards_browse_button)

        widget = QWidget()
        form = QFormLayout(widget)
        grow_fields(form)
        form.addRow("Board library", row)
        form.addRow("Board formats", self.boards_extensions)
        form.addRow("Schematic formats", self.boards_schematic_extensions)
        form.addRow("Open board in", self.boards_viewer)
        return widget

    def browse_for_board_library(self) -> None:
        """Pick the board-view library directory."""
        path = QFileDialog.getExistingDirectory(self, "Select the board library folder")
        if path:
            self.boards_library_dir.setText(path)

    def _build_logging_tab(self) -> QWidget:
        self.logging_combo = QComboBox()
        self.logging_combo.addItems(_LOG_LEVELS)
        self.logging_combo.setCurrentText(self._config.shell.logging.level)
        fit_combo_popup(self.logging_combo)
        widget = QWidget()
        form = QFormLayout(widget)
        grow_fields(form)
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
        # Picking a thermal camera is what turns thermal on; "(none)" turns it off.
        self._config.panels.thermal.enabled = bool(self._config.panels.thermal.device)
        self._config.panels.telemetry.port = self.serial_combo.currentData() or ""

        self._config.output.enabled = self.output_enabled.isChecked()
        self._config.output.device = self.output_device.text().strip()
        self._config.output.width = self.output_width.value()
        self._config.output.height = self.output_height.value()
        self._config.output.fps = self.output_fps.value()

        self._config.telemetry_overlay.enabled = self.overlay_enabled.isChecked()
        self._config.telemetry_overlay.corner = self.overlay_corner.currentData() or "bottom-left"
        self._config.telemetry_overlay.show_psu = self.overlay_show_psu.isChecked()
        self._config.telemetry_overlay.show_dmm = self.overlay_show_dmm.isChecked()
        self._config.telemetry_overlay.scale = self.overlay_scale.value()
        self._config.telemetry_overlay.opacity = self.overlay_opacity.value()
        self._config.graph_overlay.enabled = self.graph_enabled.isChecked()
        self._config.graph_overlay.corner = self.graph_corner.currentData() or "bottom-right"
        self._config.graph_overlay.window_seconds = float(self.graph_window.value())
        self._config.graph_overlay.scale = self.graph_scale.value()
        self._config.graph_overlay.opacity = self.graph_opacity.value()
        self._config.browser_sources.enabled = self.browser_sources_enabled.isChecked()
        self._config.browser_sources.port = self.browser_sources_port.value()
        self._config.terminal.enabled = self.terminal_enabled.isChecked()
        self._config.terminal.shell = self.terminal_shell.text().strip()
        self._config.terminal.share = self.terminal_share.isChecked()
        self._config.terminal.share_token = self._share_token

        self._config.faults.auto_log = self.fault_auto_log.isChecked()
        self._config.faults.current_threshold_a = self.fault_threshold.value()

        self._config.intake.base_url = self.intake_base_url.text().strip()
        self._config.intake.token = self.intake_token.text().strip()

        self._config.inventory.base_url = self.inventory_base_url.text().strip()
        self._config.inventory.token = self.inventory_token.text().strip()

        self._config.boards.library_dir = self.boards_library_dir.text().strip()
        self._config.boards.extensions = _parse_extensions(self.boards_extensions.text()) or list(
            DEFAULT_EXTENSIONS
        )
        self._config.boards.schematic_extensions = _parse_extensions(
            self.boards_schematic_extensions.text()
        ) or ["pdf"]
        self._config.boards.viewer = self.boards_viewer.currentData() or "auto"

        self._config.shell.logging.level = self.logging_combo.currentText()
        self._config.tools = self._collect_tools()


def _fill_camera_combo(combo: QComboBox, cameras: list[CameraDevice], current: str) -> None:
    combo.clear()
    combo.addItem("(none)", "")
    selected = 0 if not current else -1
    for cam in cameras:
        combo.addItem(f"{cam.label} ({cam.identifier})", cam.identifier)
        if cam.identifier == current:
            selected = combo.count() - 1
    if current and selected < 0:
        combo.addItem(f"{current} (configured, not detected)", current)
        selected = combo.count() - 1
    if selected >= 0:
        combo.setCurrentIndex(selected)
    fit_combo_popup(combo)


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
    fit_combo_popup(combo)


def _parse_extensions(text: str) -> list[str]:
    """Comma-separated formats → normalised list (``".PDF, brd"`` → ``["pdf", "brd"]``)."""
    return [part.strip().lstrip(".").lower() for part in text.split(",") if part.strip()]


def _cell(table: QTableWidget, row: int, col: int) -> str:
    item = table.item(row, col)
    return item.text().strip() if item is not None else ""


def _ratio_spin(lo: float, hi: float, value: float) -> QDoubleSpinBox:
    """A 0.1-step float spin for size/opacity ratios."""
    spin = QDoubleSpinBox()
    spin.setRange(lo, hi)
    spin.setDecimals(2)
    spin.setSingleStep(0.1)
    spin.setValue(value)
    return spin


def _spin(lo: int, hi: int, value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(lo, hi)
    spin.setValue(value)
    return spin
