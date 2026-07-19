"""The benchhud main window.

Restores geometry/dock layout from :class:`~benchhud.config.Config` and persists
on close. Hosts panels via :class:`~benchhud.shell.panel_manager.PanelManager`.

Capture is explicit, not automatic: :meth:`start_preview` builds a frame source →
:class:`LatestFrameReader` → :class:`FramePump` and drives it from a QTimer, so
constructing the window (as tests do repeatedly) spawns no threads. Until the
bench-gated ``CameraFrameSource`` lands, the default source is synthetic — so the
shell animates end to end with no camera.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QMessageBox, QWidget

from benchhud import __version__
from benchhud.capture.frame_source import FrameSource, SyntheticFrameSource
from benchhud.capture.latest_frame_reader import LatestFrameReader
from benchhud.capture.pipeline import FramePump
from benchhud.config import Config, PersistCallback
from benchhud.devices.discovery import DeviceScanner, SystemDeviceScanner
from benchhud.shell.about import about_html
from benchhud.shell.icon import app_icon
from benchhud.shell.panel_manager import PanelManager
from benchhud.shell.panels.controls import ControlsPanel
from benchhud.shell.panels.launcher import LauncherPanel
from benchhud.shell.panels.power import PowerPanel
from benchhud.shell.panels.scope import ScopePanel
from benchhud.shell.panels.thermal import ThermalPanel
from benchhud.shell.preferences import PreferencesDialog

# Preview refresh rate for the on-screen scope panel (the virtual-cam output has
# its own fps in config; this is just how often the GUI repaints).
_PREVIEW_FPS = 30

SourceFactory = Callable[[], FrameSource]


def _default_scope_source() -> FrameSource:
    """Dev fallback until the bench camera lands: a synthetic scope feed."""
    return SyntheticFrameSource(960, 540)


class MainWindow(QMainWindow):
    """Top-level HUD window."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        on_persist: PersistCallback | None = None,
        scanner: DeviceScanner | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config or Config()
        self._on_persist = on_persist
        self._scanner = scanner or SystemDeviceScanner()
        self._pump: FramePump | None = None
        self._timer: QTimer | None = None

        self.setObjectName("BenchhudMainWindow")
        self.setWindowTitle("benchhud")
        self.setWindowIcon(app_icon())
        self._build_menu()

        central = QLabel(
            "benchhud — composite / preview area\n\n"
            "The scope + thermal composite renders here once the pipeline lands (Phase 1)."
        )
        central.setAlignment(Qt.AlignmentFlag.AlignCenter)
        central.setObjectName("CompositePreview")
        self.setCentralWidget(central)

        # Panels must exist before restoreState so their dock geometry restores.
        # Default layout: cameras + meter on the right, settings tabbed on the
        # left, composite preview in the centre. A saved layout (config) wins on
        # later launches; this is just the first-run arrangement.
        self.panels = PanelManager(self)
        right = Qt.DockWidgetArea.RightDockWidgetArea
        left = Qt.DockWidgetArea.LeftDockWidgetArea

        self.scope_panel = ScopePanel()
        scope_dock = self.panels.add_panel(self.scope_panel, right)
        self.thermal_panel = ThermalPanel()
        thermal_dock = self.panels.add_panel(self.thermal_panel, right)
        self.tabifyDockWidget(scope_dock, thermal_dock)  # scope/thermal as tabs
        # PowerPanel is the combined power/DMM meter (supersedes a separate
        # telemetry dock); it renders the DMM header + V/A/W/max + trend graph.
        self.power_panel = PowerPanel()
        self.panels.add_panel(self.power_panel, right)

        self.controls_panel = ControlsPanel(self._config)
        controls_dock = self.panels.add_panel(self.controls_panel, left)
        self.launcher_panel = LauncherPanel(self._config.tools)
        launcher_dock = self.panels.add_panel(self.launcher_panel, left)
        self.tabifyDockWidget(controls_dock, launcher_dock)  # controls/tools tabs

        scope_dock.raise_()
        controls_dock.raise_()

        self._build_view_menu()
        self._default_state = self.saveState()  # for "Reset layout"

        self._refresh_status()

        self._apply_config()

    def _apply_config(self) -> None:
        shell = self._config.shell
        self.resize(shell.window_width, shell.window_height)
        if shell.layout:
            self.restoreState(QByteArray.fromBase64(shell.layout.encode("ascii")))

    # --- menu / preferences ----------------------------------------------

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("benchhud")
        self.preferences_action = QAction("Preferences…", self)
        self.preferences_action.setShortcut(QKeySequence.StandardKey.Preferences)
        self.preferences_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        self.preferences_action.triggered.connect(self.open_preferences)
        menu.addAction(self.preferences_action)

        self.about_action = QAction("About benchhud", self)
        self.about_action.setMenuRole(QAction.MenuRole.AboutRole)
        self.about_action.triggered.connect(self.open_about)
        menu.addAction(self.about_action)

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

    def _build_view_menu(self) -> None:
        menu = self.menuBar().addMenu("View")
        for panel_id in ("scope", "thermal", "power", "controls", "launcher"):
            dock = self.panels.dock(panel_id)
            if dock is not None:
                menu.addAction(dock.toggleViewAction())
        menu.addSeparator()
        self.reset_layout_action = QAction("Reset layout", self)
        self.reset_layout_action.triggered.connect(self.reset_layout)
        menu.addAction(self.reset_layout_action)

    def reset_layout(self) -> None:
        """Restore the default dock arrangement."""
        self.restoreState(self._default_state)

    def open_preferences(self) -> PreferencesDialog:
        """Open the preferences dialog. Returns it (tests inspect without exec)."""
        dialog = PreferencesDialog(
            self._config, self._scanner, on_save=self._on_persist, parent=self
        )
        dialog.accepted.connect(self._refresh_status)
        dialog.open()
        return dialog

    def open_about(self) -> QMessageBox:
        """Show the About dialog (non-blocking; returned for tests)."""
        box = QMessageBox(self)
        box.setWindowTitle("About benchhud")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(about_html())
        box.setIcon(QMessageBox.Icon.Information)
        box.show()
        return box

    def _refresh_status(self) -> None:
        panels = self._config.panels
        self.statusBar().showMessage(
            f"benchhud {__version__}   ·   "
            f"scope {panels.scope.device or '—'}   ·   "
            f"thermal {panels.thermal.device or '—'}   ·   "
            f"psu/dmm {panels.telemetry.port or '—'}"
        )

    # --- capture preview -------------------------------------------------

    def start_preview(self, source_factory: SourceFactory = _default_scope_source) -> None:
        """Start the scope preview loop (idempotent)."""
        if self._pump is not None:
            return
        reader = LatestFrameReader(source_factory())
        self._pump = FramePump(reader, consumers=[self.scope_panel.update_frame])
        self._pump.start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._pump.tick)
        self._timer.start(1000 // _PREVIEW_FPS)

    def stop_preview(self) -> None:
        """Stop the scope preview loop (idempotent)."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._pump is not None:
            self._pump.stop()
            self._pump = None

    # --- persistence -----------------------------------------------------

    def persist_into_config(self) -> None:
        """Write current window geometry + dock layout back into the config."""
        shell = self._config.shell
        shell.window_width = self.width()
        shell.window_height = self.height()
        shell.layout = bytes(self.saveState().toBase64()).decode("ascii")

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_preview()
        self.persist_into_config()
        if self._on_persist is not None:
            self._on_persist(self._config)
        super().closeEvent(event)
