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
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QMainWindow, QWidget

from benchhud import __version__
from benchhud.capture.frame_source import FrameSource, SyntheticFrameSource
from benchhud.capture.latest_frame_reader import LatestFrameReader
from benchhud.capture.pipeline import FramePump
from benchhud.config import Config, PersistCallback
from benchhud.shell.panel_manager import PanelManager
from benchhud.shell.panels.scope import ScopePanel
from benchhud.shell.panels.telemetry import TelemetryPanel

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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config or Config()
        self._on_persist = on_persist
        self._pump: FramePump | None = None
        self._timer: QTimer | None = None

        self.setObjectName("BenchhudMainWindow")
        self.setWindowTitle("benchhud")

        central = QLabel(
            "benchhud — composite / preview area\n\n"
            "The scope + thermal composite renders here once the pipeline lands (Phase 1)."
        )
        central.setAlignment(Qt.AlignmentFlag.AlignCenter)
        central.setObjectName("CompositePreview")
        self.setCentralWidget(central)

        # Panels must exist before restoreState so their dock geometry restores.
        self.panels = PanelManager(self)
        self.scope_panel = ScopePanel()
        self.panels.add_panel(self.scope_panel)
        self.telemetry_panel = TelemetryPanel()
        self.panels.add_panel(self.telemetry_panel)

        self.statusBar().showMessage(f"benchhud {__version__}")

        self._apply_config()

    def _apply_config(self) -> None:
        shell = self._config.shell
        self.resize(shell.window_width, shell.window_height)
        if shell.layout:
            self.restoreState(QByteArray.fromBase64(shell.layout.encode("ascii")))

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
