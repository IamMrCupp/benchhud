"""The benchhud main window.

Restores geometry/dock layout from :class:`~benchhud.config.Config` and persists
on close. Hosts panels via :class:`~benchhud.shell.panel_manager.PanelManager`.

Capture is explicit, not automatic: :meth:`start_preview` builds a frame source →
:class:`LatestFrameReader` → :class:`FramePump` and drives it from a QTimer, so
constructing the window (as tests do repeatedly) spawns no threads. The scope
source is a :class:`FallbackFrameSource`: the configured camera when it opens, the
synthetic pattern (with the reason in the status bar) when it doesn't — so the
shell animates end to end with no camera.
"""

from __future__ import annotations

import logging
import secrets
import sys
from collections.abc import Callable, Sequence

import cv2
from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMessageBox, QWidget

from benchhud import __version__
from benchhud.boards.library import BoardLibrary
from benchhud.boards.opener import open_board, open_schematic
from benchhud.boards.worklist import boards_to_source
from benchhud.browser_sources import BrowserSourceServer, TopicHub
from benchhud.browser_sources.payloads import graph_payload, job_payload, meter_payload
from benchhud.capture.camera import CameraFrameSource
from benchhud.capture.fallback import FallbackFrameSource, SourceStatus
from benchhud.capture.frame_source import FrameSource, SyntheticFrameSource
from benchhud.capture.latest_frame_reader import LatestFrameReader
from benchhud.capture.pipeline import FramePump
from benchhud.capture.thermal import Y16Camera
from benchhud.config import Config, PersistCallback
from benchhud.demo import DemoSignal
from benchhud.devices.discovery import (
    CameraDevice,
    DeviceScanner,
    SystemDeviceScanner,
    resolve_capture_device,
)
from benchhud.instruments.owon_spm8104 import InstrumentLimits, open_device
from benchhud.instruments.session import InstrumentSession
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.inventory import (
    HttpInventorySource,
    InventorySource,
    NullInventorySource,
    Part,
)
from benchhud.jobs.capture import CaptureContext
from benchhud.jobs.fault import FaultMonitor, format_fault_note
from benchhud.jobs.store import JobStore, default_db_path
from benchhud.output.sink import OutputSink, open_output_sink
from benchhud.overlay.composite import colorize, compose
from benchhud.overlay.graph import draw_graph_overlay
from benchhud.overlay.telemetry import Footprint, MeterReadout, draw_telemetry_overlay_sized
from benchhud.shell.about import about_html
from benchhud.shell.board_dialogs import BoardSearchDialog, BoardsToSourceDialog
from benchhud.shell.composite_view import CompositeView
from benchhud.shell.icon import app_icon
from benchhud.shell.panel_manager import PanelManager
from benchhud.shell.panels.controls import ControlsPanel
from benchhud.shell.panels.inventory import InventoryPanel
from benchhud.shell.panels.jobs import JobsPanel
from benchhud.shell.panels.launcher import LauncherPanel
from benchhud.shell.panels.power import PowerPanel
from benchhud.shell.panels.scope import ScopePanel
from benchhud.shell.panels.terminal import TerminalPanel, ViewFactory
from benchhud.shell.panels.thermal import ThermalPanel
from benchhud.shell.preferences import PreferencesDialog
from benchhud.terminal import SUPPORTED as TERMINAL_SUPPORTED
from benchhud.terminal import TerminalHost, login_shell
from benchhud.tickets import HttpTicketSource, NullTicketSource, TicketSource

# Preview refresh rate for the on-screen scope panel (the virtual-cam output has
# its own fps in config; this is just how often the GUI repaints).
_PREVIEW_FPS = 30

# Bump when the default dock arrangement changes. The shell auto-saves the
# layout on close, so without this a layout from an older build would silently
# override a newer default (and the user never knowingly saved one).
LAYOUT_VERSION = 4

SourceFactory = Callable[[], FrameSource]
#: Takes ``(device, width, height)`` and returns an open :class:`OutputSink`.
SinkFactory = Callable[[str, int, int], OutputSink]
#: Builds the live instrument session, or None when there's no instrument.
InstrumentSessionFactory = Callable[[], "InstrumentSession | None"]

log = logging.getLogger(__name__)


def camera_open_hint(platform: str = sys.platform) -> str:
    """What to check when a camera won't open, in this OS's terms."""
    if platform == "darwin":
        return "allow benchhud under System Settings → Privacy & Security → Camera"
    if platform.startswith("linux"):
        return "check the /dev/videoN path, that nothing else has it open, and the video group"
    return "check it's plugged in and no other app is using it"


def camera_label(device: str, cameras: Sequence[CameraDevice] = ()) -> str:
    """A human name for a configured camera: its scanned label, else the raw id."""
    text = device.strip()
    for camera in cameras:
        if camera.identifier == text:
            return camera.label
    return text


def scope_source_for(device: str, cameras: Sequence[CameraDevice] = ()) -> FallbackFrameSource:
    """The scope feed: the configured camera, or the synthetic pattern until it opens.

    A camera that is unplugged, busy (UVC allows one reader), not yet allowed by
    macOS, or simply wrong in the config must leave the shell usable — so we
    fall back rather than fail, keep retrying, and report why in ``status``.
    ``cameras`` is the current scan, used to resolve a Qt device id to an index.
    """
    fallback = SyntheticFrameSource(960, 540)
    if not device.strip():
        return FallbackFrameSource(None, fallback)
    camera = CameraFrameSource(resolve_capture_device(device, cameras))
    return FallbackFrameSource(
        camera,
        fallback,
        name=camera_label(device, cameras),
        open_hint=camera_open_hint(),
    )


def thermal_source_for(device: str, cameras: Sequence[CameraDevice] = ()) -> FallbackFrameSource:
    """The thermal feed as °C arrays, with no test pattern when it's down.

    Unlike the scope, the thermal panel never shows a stand-in: a fake heatmap
    with a temperature on it could be taken for a real reading.
    """
    if not device.strip():
        return FallbackFrameSource(None, None)
    camera = Y16Camera(resolve_capture_device(device, cameras))
    return FallbackFrameSource(
        camera,
        None,
        name=camera_label(device, cameras),
        open_hint=camera_open_hint(),
    )


class MainWindow(QMainWindow):
    """Top-level HUD window."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        on_persist: PersistCallback | None = None,
        scanner: DeviceScanner | None = None,
        job_store: JobStore | None = None,
        terminal_view_factory: ViewFactory | None = None,
        terminal_session_factory=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config or Config()
        self._terminal: TerminalHost | None = None
        self._terminal_session_factory = terminal_session_factory
        self._on_persist = on_persist
        self._scanner = scanner or SystemDeviceScanner()
        self._job_store = job_store or JobStore(default_db_path())
        self._pump: FramePump | None = None
        self._timer: QTimer | None = None
        self._scope_source: FrameSource | None = None
        self._scope_status: SourceStatus | None = None
        self._preview_device: str | None = None
        self._thermal_pump: FramePump | None = None
        self._thermal_timer: QTimer | None = None
        self._thermal_source: FrameSource | None = None
        self._thermal_status: SourceStatus | None = None
        self._thermal_device: str | None = None
        self._last_temps = None
        self._cameras: list[CameraDevice] = []
        self._sink: OutputSink | None = None
        # The hub outlives server restarts, so a page reconnecting after a
        # Preferences change gets the current readings straight away.
        self._browser_hub = TopicHub()
        self._browser_server: BrowserSourceServer | None = None
        self._browser_warning: str | None = None
        self._demo: DemoSignal | None = None
        self._demo_timer: QTimer | None = None
        self._demo_t = 0.0
        self._demo_scope = SyntheticFrameSource(960, 540)
        self._instrument_session: InstrumentSession | None = None
        self._instrument_timer: QTimer | None = None
        self._instrument_t = 0.0
        self._fault_monitor = FaultMonitor(self._config.faults.current_threshold_a)
        self._ticket_source: TicketSource = self._make_ticket_source()
        self._inventory_source: InventorySource = self._make_inventory_source()
        self._board_library = self._make_board_library()

        self.setObjectName("BenchhudMainWindow")
        self.setWindowTitle("benchhud")
        self.setWindowIcon(app_icon())
        self._build_menu()

        self.composite_view = CompositeView()
        self.composite_view.setObjectName("CompositePreview")
        self.setCentralWidget(self.composite_view)

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
        # PowerPanel is the numeric power/DMM meter (DMM header + V/A/W/max).
        self.power_panel = PowerPanel()
        self.panels.add_panel(self.power_panel, right)
        self.controls_panel = ControlsPanel(self._config)
        controls_dock = self.panels.add_panel(self.controls_panel, left)
        self.launcher_panel = LauncherPanel(self._config.tools)
        launcher_dock = self.panels.add_panel(self.launcher_panel, left)
        self.tabifyDockWidget(controls_dock, launcher_dock)  # controls/tools tabs
        self.controls_panel.changed.connect(self.render_composite)
        self.jobs_panel = JobsPanel(
            self._job_store,
            capture_provider=self.capture_context,
            ticket_source=lambda: self._ticket_source,
            board_library=lambda: self._board_library,
            open_board=self.open_board_file,
            open_schematic=self.open_schematic_file,
        )
        self.jobs_panel.job_changed.connect(self._publish_job)
        jobs_dock = self.panels.add_panel(self.jobs_panel, left)
        self.tabifyDockWidget(launcher_dock, jobs_dock)  # …and jobs
        self.inventory_panel = InventoryPanel(
            lambda: self._inventory_source,
            job_ref=self.selected_job_ref,
            on_consumed=self._note_parts_used,
        )
        inventory_dock = self.panels.add_panel(self.inventory_panel, left)
        self.tabifyDockWidget(jobs_dock, inventory_dock)  # …and inventory
        self.terminal_panel = TerminalPanel(terminal_view_factory)
        self.terminal_panel.restart_requested.connect(self.restart_terminal)
        self.terminal_panel.pause_toggled.connect(self.set_terminal_paused)
        terminal_dock = self.panels.add_panel(self.terminal_panel, left)
        self.tabifyDockWidget(inventory_dock, terminal_dock)  # …and the terminal

        scope_dock.raise_()
        controls_dock.raise_()

        self._update_terminal_panel()

        self._build_boards_menu()
        self._build_view_menu()
        self._default_state = self.saveState()  # for "Reset layout"

        self._refresh_status()

        self._apply_config()

    def _apply_config(self) -> None:
        shell = self._config.shell
        self._resize_within_screen(shell.window_width, shell.window_height)
        # Only restore a layout saved by the *current* default arrangement;
        # a stale one is discarded so the newer default wins.
        if shell.layout and shell.layout_version == LAYOUT_VERSION:
            self.restoreState(QByteArray.fromBase64(shell.layout.encode("ascii")))

    def _resize_within_screen(self, width: int, height: int) -> None:
        """Resize to (width, height) but never larger than the screen; centre it."""
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            width = min(width, avail.width())
            height = min(height, avail.height())
            self.resize(width, height)
            frame = self.frameGeometry()
            frame.moveCenter(avail.center())
            self.move(frame.topLeft())
        else:
            self.resize(width, height)

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

    def _build_boards_menu(self) -> None:
        menu = self.menuBar().addMenu("Boards")
        self.find_board_action = QAction("Find board…", self)
        self.find_board_action.setToolTip("Search the local board-view library")
        self.find_board_action.triggered.connect(self.open_board_search)
        menu.addAction(self.find_board_action)

        self.boards_to_source_action = QAction("Boards to source…", self)
        self.boards_to_source_action.setToolTip("Open tickets whose board isn't in the library")
        self.boards_to_source_action.triggered.connect(self.open_boards_to_source)
        menu.addAction(self.boards_to_source_action)

    def _build_view_menu(self) -> None:
        menu = self.menuBar().addMenu("View")
        for panel_id in (
            "scope",
            "thermal",
            "power",
            "controls",
            "launcher",
            "jobs",
            "inventory",
            "terminal",
        ):
            dock = self.panels.dock(panel_id)
            if dock is not None:
                menu.addAction(dock.toggleViewAction())
        menu.addSeparator()
        self.reset_layout_action = QAction("Reset layout", self)
        self.reset_layout_action.triggered.connect(self.reset_layout)
        menu.addAction(self.reset_layout_action)

        menu.addSeparator()
        self.demo_action = QAction("Demo mode", self)
        self.demo_action.setCheckable(True)
        self.demo_action.toggled.connect(self.set_demo_mode)
        menu.addAction(self.demo_action)

    def reset_layout(self) -> None:
        """Restore the default dock arrangement and drop the saved layout."""
        self.restoreState(self._default_state)
        self._config.shell.layout = None
        self._config.shell.layout_version = LAYOUT_VERSION

    # --- demo mode -------------------------------------------------------

    def set_demo_mode(self, enabled: bool) -> None:
        """Toggle demo mode (synthetic data across the whole HUD).

        Demo and the live poll both feed :meth:`on_telemetry`, so exactly one of
        them runs — otherwise the meter alternates between real and synthetic
        readings, which is worse than either.
        """
        if enabled:
            self.stop_telemetry()
            self._start_demo()
        else:
            self._stop_demo()
            self.start_telemetry()

    def _start_demo(self) -> None:
        if self._demo_timer is not None:
            return
        self._demo = DemoSignal()
        self._demo_t = 0.0
        self.start_preview()  # animate the scope too
        self._demo_timer = QTimer(self)
        self._demo_timer.timeout.connect(self._demo_tick)
        self._demo_timer.start(200)  # 5 Hz
        self._refresh_status()

    def _demo_tick(self) -> None:
        if self._demo is None:
            return
        self._demo.step()
        self._demo_t += 0.2
        snapshot = self._demo.telemetry()
        # Demo drives a synthetic scope feed too, so the centre shows a real
        # composite (scope + thermal) rather than thermal on its own.
        if self._pump is None:
            self.scope_panel.update_frame(self._demo_scope.read())
        if not self._thermal_live():
            self.thermal_panel.update_temps(self._demo.thermal())
        self.on_telemetry(snapshot, self._demo_t)
        self.render_composite()

    def on_telemetry(self, snapshot: TelemetrySnapshot, t: float = 0.0) -> None:
        """Single entry point for a telemetry poll (demo now, bench poll later).

        Feeds the meter, then runs over-current fault detection so a shorted
        board logs itself to the active job.
        """
        self.power_panel.update_from_snapshot(snapshot, t)
        self._publish_telemetry()
        if snapshot.current is not None:
            self._check_fault(snapshot.current)

    def _check_fault(self, current: float) -> None:
        """Auto-log an over-current fault to the selected job, when enabled."""
        if not self._config.faults.auto_log:
            return
        event = self._fault_monitor.observe(current)
        if event is None:
            return
        job_id = self.jobs_panel.selected_job_id()
        if job_id is None:
            return  # nothing selected to attach the fault to
        self._job_store.add_note(job_id, format_fault_note(event))
        self.jobs_panel.refresh_notes()

    def reconfigure_faults(self) -> None:
        """Rebuild the fault monitor after a Preferences change."""
        self._fault_monitor = FaultMonitor(self._config.faults.current_threshold_a)

    def _make_ticket_source(self) -> TicketSource:
        """Build the ticket source from config: intake when set, else standalone."""
        intake = self._config.intake
        if intake.base_url.strip():
            return HttpTicketSource(intake.base_url, intake.token)
        return NullTicketSource()

    def reconfigure_ticket_source(self) -> None:
        """Rebuild the ticket source after a Preferences change."""
        self._ticket_source = self._make_ticket_source()

    def _make_inventory_source(self) -> InventorySource:
        """Build the inventory source from config: bench-parts when set, else standalone."""
        inventory = self._config.inventory
        if inventory.base_url.strip():
            return HttpInventorySource(inventory.base_url, inventory.token)
        return NullInventorySource()

    def reconfigure_inventory_source(self) -> None:
        """Rebuild the inventory source after a Preferences change."""
        self._inventory_source = self._make_inventory_source()
        self.inventory_panel.refresh()

    def selected_job_ref(self) -> str | None:
        """The selected job's shop-ticket ref — what consumption is billed to.

        None when no job is selected or the job isn't linked to a ticket;
        bench-parts records the movement without a ref in that case.
        """
        job_id = self.jobs_panel.selected_job_id()
        if job_id is None:
            return None
        job = self._job_store.get_job(job_id)
        return job.external_ref if job is not None else None

    def _note_parts_used(self, part: Part, quantity: int) -> None:
        """Drop a note on the selected job's timeline after a consume.

        The shop ledger is the source of truth; this is the bench-side echo so
        the job report tells the whole story without opening bench-parts.
        """
        job_id = self.jobs_panel.selected_job_id()
        if job_id is None:
            return
        name = part.mpn or part.description
        self._job_store.add_note(job_id, f"Used {quantity} \u00d7 {name}")
        self.jobs_panel.refresh_notes()

    # --- board docs ------------------------------------------------------

    def _make_board_library(self) -> BoardLibrary:
        boards = self._config.boards
        return BoardLibrary.from_config(
            boards.library_dir, boards.extensions, boards.schematic_extensions
        )

    def reconfigure_boards(self) -> None:
        """Rebuild the board library after a Preferences change."""
        self._board_library = self._make_board_library()

    def open_board_file(self, path) -> bool:
        """Open a board file in the configured viewer (or the OS handler)."""
        return open_board(path, self._config.tools, prefer=self._config.boards.viewer)

    def open_schematic_file(self, path) -> bool:
        """Open a schematic PDF on its own via the OS handler."""
        return open_schematic(path)

    def open_board_search(self) -> BoardSearchDialog:
        """ "Do I have this board?" — search the library (returned for tests)."""
        dialog = BoardSearchDialog(
            self._board_library, open_board=self.open_board_file, parent=self
        )
        dialog.open()
        return dialog

    def open_boards_to_source(self) -> BoardsToSourceDialog:
        """Boards the open tickets need that the library lacks (for tests)."""
        needs = boards_to_source(self._ticket_source.list_open_tickets(), self._board_library)
        dialog = BoardsToSourceDialog(needs, parent=self)
        dialog.open()
        return dialog

    def _stop_demo(self) -> None:
        if self._demo_timer is not None:
            self._demo_timer.stop()
            self._demo_timer = None
        self._demo = None
        self._refresh_status()

    @property
    def demo_active(self) -> bool:
        return self._demo_timer is not None

    def open_preferences(self) -> PreferencesDialog:
        """Open the preferences dialog. Returns it (tests inspect without exec)."""
        dialog = PreferencesDialog(
            self._config, self._scanner, on_save=self._on_persist, parent=self
        )
        dialog.accepted.connect(self.restart_output)
        dialog.accepted.connect(self.reconfigure_preview)
        dialog.accepted.connect(self.reconfigure_thermal)
        dialog.accepted.connect(self.reconfigure_faults)
        dialog.accepted.connect(self.reconfigure_ticket_source)
        dialog.accepted.connect(self.reconfigure_inventory_source)
        dialog.accepted.connect(self.reconfigure_boards)
        dialog.accepted.connect(self.reconfigure_tools)
        dialog.accepted.connect(self.reconfigure_instrument_session)
        dialog.accepted.connect(self.restart_browser_sources)
        # Last: the status line reports sink state, so it must run after the
        # sink has been rebuilt or it shows the previous one.
        dialog.accepted.connect(self._refresh_status)
        dialog.open()
        return dialog

    def reconfigure_tools(self) -> None:
        """Rebuild the Tools pane from config (after Preferences edits it)."""
        self.launcher_panel.set_tools(self._config.tools)

    def open_about(self) -> QMessageBox:
        """Show the About dialog (non-blocking; returned for tests)."""
        box = QMessageBox(self)
        box.setWindowTitle("About benchhud")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(about_html())
        box.setIcon(QMessageBox.Icon.Information)
        box.show()
        return box

    def _scan_cameras(self) -> list[CameraDevice]:
        """Current camera list; a scanner blowing up must not stop the preview."""
        try:
            return self._scanner.cameras()
        except Exception:
            return []

    def _on_scope_frame(self, frame) -> None:
        self.scope_panel.update_frame(frame)
        self.render_composite()

    def render_composite(self) -> None:
        """Recompose the central view from whatever feeds are live."""
        scope = self.scope_panel.last_frame
        temps = self.thermal_panel.last_temps
        if scope is None and temps is None:
            return
        if temps is None:
            self._show_composite(scope)
            return

        settings = self.controls_panel.current_settings()
        if scope is None:
            # Thermal only — show it colourised on its own.
            self._show_composite(colorize(temps, settings.tmin, settings.tmax, settings.colormap))
            return
        transform = self.controls_panel.current_transform()
        self._show_composite(compose(scope, temps, transform, settings))

    def _show_composite(self, frame) -> None:
        """Put one composed frame on screen and, if open, out to the sink.

        The telemetry overlay is applied here — the single point every composite
        path (scope-only, thermal-only, fused) flows through — so the on-screen
        preview and the virtual-cam output stay identical (WYSIWYG).
        """
        frame = self._apply_overlays(frame)
        self.composite_view.show_frame(frame)
        self._publish_frame(frame)

    def _apply_overlays(self, frame):
        """Burn the on-stream overlays onto the frame: meter readout, then power graph.

        When both share a corner, the graph stacks past the readout rather than
        covering it, at the readout's width so their edges line up (#196).
        """
        meter = self._config.telemetry_overlay
        graph = self._config.graph_overlay
        footprint = Footprint(0, 0)
        if meter.enabled:
            frame, footprint = draw_telemetry_overlay_sized(frame, self._meter_readout(), meter)
        if graph.enabled:
            stacked = meter.enabled and meter.corner == graph.corner and footprint.width > 0
            frame = draw_graph_overlay(
                frame,
                self.power_panel.chart.history.samples(),
                graph,
                stack=footprint.height if stacked else 0,
                width=footprint.width if stacked else None,
                margin=footprint.margin if stacked else None,
            )
        return frame

    def _meter_readout(self) -> MeterReadout:
        """Snapshot the meter panel's formatted strings for the overlay."""
        panel = self.power_panel
        return MeterReadout(
            voltage=panel.voltage_text,
            current=panel.current_text,
            power=panel.power_text,
            max_current=panel.max_current_text,
            dmm_mode=panel.dmm_mode_text,
            dmm_value=panel.dmm_text,
            dmm_unit=panel.dmm_unit_text,
        )

    def capture_context(self) -> CaptureContext:
        """Current bench state for capture-to-job: scope + thermal + readings."""
        return CaptureContext(
            scope=self.scope_panel.last_frame,
            temps=self.thermal_panel.last_temps,
            snapshot=self.power_panel.last_snapshot,
        )

    def output_warning(self) -> str | None:
        """Why the virtual-camera output isn't actually publishing, if it isn't.

        Output enabled + frames going nowhere is the one failure the operator
        must not discover mid-stream, so it gets said out loud.
        """
        if not self._config.output.enabled:
            return None  # deliberately off — nothing to warn about
        reason = getattr(self._sink, "reason", "")
        return reason or None

    def scope_status(self) -> SourceStatus | None:
        """Is the scope preview showing the camera, and if not, why (None: no preview)."""
        return getattr(self._scope_source, "status", None)

    def _scope_status_text(self) -> str:
        device = self._config.panels.scope.device
        label = camera_label(device, self._cameras) if device.strip() else "—"
        status = self.scope_status()
        if status is None or status.live:
            return label
        return f"{label} (⚠ test pattern — {status.reason})"

    def thermal_status(self) -> SourceStatus | None:
        """Is the thermal panel showing the camera, and if not, why (None: thermal off)."""
        return getattr(self._thermal_source, "status", None)

    def _thermal_live(self) -> bool:
        status = self.thermal_status()
        return status is not None and status.live

    def _thermal_status_text(self) -> str:
        thermal = self._config.panels.thermal
        if not (thermal.enabled and thermal.device.strip()):
            return "—"
        label = camera_label(thermal.device, self._cameras)
        status = self.thermal_status()
        if status is None or status.live:
            return label
        return f"{label} (⚠ no signal — {status.reason})"

    def _refresh_status(self) -> None:
        panels = self._config.panels
        prefix = "◉ DEMO MODE   ·   " if self.demo_active else ""
        warning = self.output_warning()
        suffix = f"   ·   ⚠ virtual cam inactive — {warning}" if warning else ""
        if self.browser_sources_warning():
            suffix += f"   ·   ⚠ local server off — {self._browser_warning}"
        self.statusBar().showMessage(
            f"{prefix}benchhud {__version__}   ·   "
            f"scope {self._scope_status_text()}   ·   "
            f"thermal {self._thermal_status_text()}   ·   "
            f"psu/dmm {panels.telemetry.port or '—'}"
            f"{suffix}"
        )

    # --- capture preview -------------------------------------------------

    def start_preview(self, source_factory: SourceFactory | None = None) -> None:
        """Start the scope preview loop (idempotent).

        Without a factory, opens the scope camera configured in Preferences.
        """
        if self._pump is not None:
            return
        device = self._config.panels.scope.device
        if source_factory is None:
            cameras = self._scan_cameras()
            self._cameras = cameras

            def source_factory() -> FrameSource:
                return scope_source_for(device, cameras)

        self._preview_device = device
        self._scope_source = source_factory()
        reader = LatestFrameReader(self._scope_source)
        self._pump = FramePump(reader, consumers=[self._on_scope_frame])
        self._pump.start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._preview_tick)
        self._timer.start(1000 // _PREVIEW_FPS)
        self._refresh_status()

    def _preview_tick(self) -> None:
        if self._pump is None:
            return
        self._pump.tick()
        # The source changes state on the reader thread (camera allowed, lost,
        # back); repaint the status bar only when it actually changed.
        status = self.scope_status()
        if status != self._scope_status:
            self._scope_status = status
            self._refresh_status()

    def reconfigure_preview(self) -> None:
        """Reopen the scope after Preferences, if the chosen camera changed."""
        if self._pump is None or self._config.panels.scope.device == self._preview_device:
            return
        self.stop_preview()
        self.start_preview()

    # --- thermal capture -------------------------------------------------

    def start_thermal(self, source_factory: SourceFactory | None = None) -> None:
        """Start reading the thermal camera (idempotent).

        Without a factory this is a no-op unless a thermal camera is chosen in
        Preferences, so a scope-only bench never touches a second device.
        """
        if self._thermal_pump is not None:
            return
        thermal = self._config.panels.thermal
        if source_factory is None:
            if not (thermal.enabled and thermal.device.strip()):
                return
            cameras = self._cameras or self._scan_cameras()
            self._cameras = cameras
            device = thermal.device

            def source_factory() -> FrameSource:
                return thermal_source_for(device, cameras)

        self._thermal_device = thermal.device
        self._thermal_source = source_factory()
        reader = LatestFrameReader(self._thermal_source)
        self._thermal_pump = FramePump(reader, consumers=[self._on_thermal_frame])
        self._thermal_pump.start()
        self._thermal_timer = QTimer(self)
        self._thermal_timer.timeout.connect(self._thermal_tick)
        self._thermal_timer.start(1000 // _PREVIEW_FPS)
        self._refresh_status()

    def _on_thermal_frame(self, temps) -> None:
        if temps is self._last_temps:
            return  # the pump re-offers the latest frame every tick
        self._last_temps = temps
        self.thermal_panel.update_temps(temps)
        if self._pump is None:
            self.render_composite()  # otherwise the scope tick renders it

    def _thermal_tick(self) -> None:
        if self._thermal_pump is None:
            return
        self._thermal_pump.tick()
        status = self.thermal_status()
        if status != self._thermal_status:
            was_live = self._thermal_status is not None and self._thermal_status.live
            self._thermal_status = status
            if was_live and not self.demo_active:
                self.thermal_panel.clear()
                self.render_composite()
            self._refresh_status()

    def stop_thermal(self) -> None:
        """Stop reading the thermal camera (idempotent)."""
        if self._thermal_timer is not None:
            self._thermal_timer.stop()
            self._thermal_timer = None
        if self._thermal_pump is not None:
            self._thermal_pump.stop()
            self._thermal_pump = None
        self._thermal_source = None
        self._thermal_status = None
        self._thermal_device = None
        self._last_temps = None

    def reconfigure_thermal(self) -> None:
        """Apply a thermal camera change from Preferences (start, stop or switch)."""
        thermal = self._config.panels.thermal
        wanted = thermal.device if (thermal.enabled and thermal.device.strip()) else None
        if wanted == self._thermal_device:
            return
        if self._thermal_pump is not None:
            self.stop_thermal()
            if not self.demo_active:
                self.thermal_panel.clear()
                self.render_composite()
        self.start_thermal()

    # --- live instrument telemetry ---------------------------------------

    def _make_instrument_session(self) -> InstrumentSession | None:
        """Build the live session from config, or None when there's no instrument.

        Forgiving like :class:`CameraFrameSource`: an unplugged instrument, a
        missing pyserial or a bad port must leave the HUD running on synthetic
        data, not refuse to start.
        """
        telemetry = self._config.panels.telemetry
        if not telemetry.enabled or not telemetry.port.strip():
            return None
        instruments = self._config.instruments
        try:
            driver = open_device(
                telemetry.port,
                baud=telemetry.baud,
                limits=InstrumentLimits(instruments.max_voltage, instruments.max_current),
                control_enabled=instruments.control,
            )
        except Exception as exc:
            log.warning("no instrument on %s: %s", telemetry.port, exc)
            return None
        return InstrumentSession(driver, poll_interval=instruments.poll_interval)

    def start_telemetry(self, session_factory: InstrumentSessionFactory | None = None) -> None:
        """Start polling the bench instrument (idempotent).

        This is what finally makes ``read_snapshot`` reachable — until now the
        meter has only ever been fed by :class:`DemoSignal`.
        """
        if self._instrument_timer is not None or self.demo_active:
            return
        factory = session_factory or self._make_instrument_session
        session = factory()
        if session is None:
            return  # nothing configured or nothing plugged in — not an error
        self._instrument_session = session
        self._instrument_t = 0.0
        session.start()
        # The session polls on its own thread; this timer only reads the latest
        # snapshot, so a wedged port can never stall the GUI.
        interval = self._config.instruments.poll_interval
        self._instrument_timer = QTimer(self)
        self._instrument_timer.timeout.connect(self._telemetry_tick)
        self._instrument_timer.start(max(50, int(interval * 1000)))

    def _telemetry_tick(self) -> None:
        session = self._instrument_session
        if session is None:
            return
        snapshot = session.latest()
        if snapshot is None:
            return  # first poll hasn't landed yet
        self._instrument_t += self._config.instruments.poll_interval
        self.on_telemetry(snapshot, self._instrument_t)

    def stop_telemetry(self) -> None:
        """Stop polling and drop the rail (idempotent)."""
        if self._instrument_timer is not None:
            self._instrument_timer.stop()
            self._instrument_timer = None
        if self._instrument_session is not None:
            # stop() runs the output-off invariant before closing the port.
            self._instrument_session.stop()
            self._instrument_session = None

    def reconfigure_instrument_session(self) -> None:
        """Rebuild the live session after a Preferences change."""
        self.stop_telemetry()
        self.start_telemetry()

    @property
    def telemetry_active(self) -> bool:
        return self._instrument_timer is not None

    def stop_preview(self) -> None:
        """Stop the scope preview loop (idempotent)."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._pump is not None:
            self._pump.stop()
            self._pump = None
        self._scope_source = None
        self._scope_status = None
        self._preview_device = None

    # --- virtual-camera output -------------------------------------------

    def start_output(self, sink_factory: SinkFactory | None = None) -> None:
        """Open the virtual-camera sink, if output is enabled (idempotent).

        A sink that won't open is not fatal — ``open_output_sink`` falls back to
        a discarding sink and logs, so the HUD runs the same on a bench with no
        loopback module as one with it.
        """
        if self._sink is not None or not self._config.output.enabled:
            return
        out = self._config.output
        factory = sink_factory or open_output_sink
        self._sink = factory(out.device, out.width, out.height)
        self._refresh_status()

    def stop_output(self) -> None:
        """Close the virtual-camera sink (idempotent)."""
        if self._sink is None:
            return
        self._sink.close()
        self._sink = None

    def restart_output(self) -> None:
        """Reopen the sink so a Preferences change takes effect immediately.

        Cheap enough to do unconditionally — closing a loopback fd and opening
        it again costs nothing, and it means the enable checkbox, device, and
        geometry all apply without a restart.
        """
        self.stop_output()
        self.start_output()

    def _publish_frame(self, frame) -> None:
        """Send one composed frame to the sink, resized to its geometry.

        The sink's size is fixed by config; the composite is whatever size the
        scope gives us, so it's scaled to fit. A sink that fails mid-stream (the
        module unloaded, OBS gone) is dropped rather than raising into the Qt
        slot on every frame.
        """
        if self._sink is None:
            return
        width, height = self._sink.size
        if frame.shape[1] != width or frame.shape[0] != height:
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        try:
            self._sink.send(frame)
        except Exception:
            log.exception("virtual-camera output failed; closing the sink")
            self.stop_output()

    # --- OBS browser sources (#180) -----------------------------------------

    @property
    def browser_sources(self) -> BrowserSourceServer | None:
        """The running browser-source server, or None when it's off."""
        return self._browser_server

    def _server_wanted(self) -> bool:
        """The local server runs for the browser sources, the terminal, or both."""
        return self._config.browser_sources.enabled or self._terminal is not None

    def browser_sources_warning(self) -> str | None:
        """Why the local server isn't serving when it should be (the port is taken)."""
        if not self._server_wanted():
            return None
        return self._browser_warning

    def start_browser_sources(self) -> None:
        """Start the loopback server for the browser sources and/or the terminal
        (idempotent; a no-op when neither is on).

        A taken port isn't fatal: it's said in the status bar, the same way a
        virtual camera that won't open is, and the HUD carries on.
        """
        self._sync_terminal()
        if self._browser_server is None and self._server_wanted():
            cfg = self._config.browser_sources
            server = BrowserSourceServer(
                self._browser_hub, cfg.port, panels=cfg.enabled, terminal=self._terminal
            )
            try:
                server.start()
            except OSError as exc:
                log.warning("local server could not listen on port %d: %s", cfg.port, exc)
                self._browser_warning = f"port {cfg.port} is in use ({exc.strerror or exc})"
            else:
                self._browser_server = server
                self._browser_warning = None
                self._publish_telemetry()
                self._publish_job()
        self._update_terminal_panel()
        self._refresh_status()

    def stop_browser_sources(self) -> None:
        """Stop serving and drop every connected page (idempotent). The shell keeps running."""
        self._browser_warning = None
        if self._browser_server is None:
            return
        self._browser_server.stop()
        self._browser_server = None

    def restart_browser_sources(self) -> None:
        """Apply a Preferences change (enable, port, terminal) without a relaunch."""
        self.stop_browser_sources()
        self.start_browser_sources()

    # --- terminal (#194) -------------------------------------------------------

    @property
    def terminal(self) -> TerminalHost | None:
        """The running terminal, or None when it's off."""
        return self._terminal

    def _sync_terminal(self) -> None:
        """Bring the shell in line with config.

        Only turning the terminal off, or changing its shell, ends the running
        shell: pressing OK in Preferences for anything else must not.
        """
        cfg = self._config.terminal
        if not (cfg.enabled and TERMINAL_SUPPORTED):
            self.stop_terminal()
            return
        if not cfg.share_token:
            cfg.share_token = secrets.token_urlsafe(18)
        argv = login_shell(cfg.shell)
        if self._terminal is not None and self._terminal.argv != argv:
            self.stop_terminal()
        if self._terminal is None:
            kwargs = {}
            if self._terminal_session_factory is not None:
                kwargs["session_factory"] = self._terminal_session_factory
            self._terminal = TerminalHost(argv, share_token=cfg.share_token, **kwargs)
            self._terminal.start()
        self._terminal.share_token = cfg.share_token
        self._terminal.set_sharing(cfg.share)

    def stop_terminal(self) -> None:
        """End the shell (idempotent)."""
        if self._terminal is None:
            return
        self._terminal.close()
        self._terminal = None

    def restart_terminal(self) -> None:
        """The panel's Restart button: a fresh shell, same page."""
        if self._terminal is not None:
            self._terminal.restart()

    def set_terminal_paused(self, paused: bool) -> None:
        """The panel's Pause button: hide the terminal from the stream, or bring it back."""
        if self._terminal is not None:
            self._terminal.set_paused(paused)
        self._update_terminal_panel()

    def _update_terminal_panel(self) -> None:
        panel = self.terminal_panel
        cfg = self._config.terminal
        terminal = self._terminal
        if not TERMINAL_SUPPORTED:
            panel.show_message("The terminal isn't available on Windows yet.")
        elif not cfg.enabled:
            panel.show_message("The terminal is off. Turn it on in Preferences → Terminal.")
        elif self._browser_warning:
            panel.show_message(f"The terminal can't start: {self._browser_warning}.")
        elif terminal is None or self._browser_server is None:
            panel.show_message("Starting the terminal…")
        else:
            panel.show_terminal(self._browser_server.terminal_url(terminal.write_token))
        state = terminal.state() if terminal is not None else None
        panel.set_share_state(
            sharing=bool(state and state.sharing), paused=bool(state and state.paused)
        )

    def _publish_telemetry(self) -> None:
        """Hand the meter and graph pages the readings the docks just drew."""
        if self._browser_server is None:
            return
        self._browser_hub.publish("meter", meter_payload(self._meter_readout()))
        self._browser_hub.publish(
            "graph",
            graph_payload(
                self.power_panel.chart.history.samples(),
                self._config.graph_overlay.window_seconds,
            ),
        )

    def _publish_job(self) -> None:
        """Hand the job-card page the selected job (its whitelisted fields only)."""
        if self._browser_server is None:
            return
        job_id = self.jobs_panel.selected_job_id()
        job = self._job_store.get_job(job_id) if job_id is not None else None
        self._browser_hub.publish("job", job_payload(job))

    # --- persistence -----------------------------------------------------

    def persist_into_config(self) -> None:
        """Write current window geometry + dock layout back into the config."""
        shell = self._config.shell
        shell.window_width = self.width()
        shell.window_height = self.height()
        shell.layout = bytes(self.saveState().toBase64()).decode("ascii")
        shell.layout_version = LAYOUT_VERSION

    def closeEvent(self, event: QCloseEvent) -> None:
        self._stop_demo()
        self.stop_telemetry()
        self.stop_preview()
        self.stop_thermal()
        self.stop_output()
        self.stop_browser_sources()
        self.stop_terminal()
        self.persist_into_config()
        if self._on_persist is not None:
            self._on_persist(self._config)
        super().closeEvent(event)
