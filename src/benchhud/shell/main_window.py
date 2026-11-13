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
import os
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime

import cv2
import numpy as np
from PySide6.QtCore import QByteArray, QChildEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabBar, QWidget

from benchhud import __version__
from benchhud.assets import AssetSource, HttpAssetSource, NullAssetSource
from benchhud.boards.library import BoardLibrary
from benchhud.boards.opener import open_board, open_schematic
from benchhud.boards.worklist import boards_to_source
from benchhud.browser_sources import BrowserSourceServer, TopicHub
from benchhud.browser_sources.payloads import graph_payload, job_payload, meter_payload
from benchhud.capture.camera import CameraFrameSource
from benchhud.capture.fallback import FallbackFrameSource, SourceStatus
from benchhud.capture.frame_source import FrameSource, SyntheticFrameSource
from benchhud.capture.latest_frame_reader import LatestFrameReader
from benchhud.capture.modes import auto_size, probed_modes
from benchhud.capture.pipeline import FramePump
from benchhud.capture.thermal import Y16Camera
from benchhud.capture.uvc_driver import TORN_FRAMES_WARNING, passes_torn_frames
from benchhud.config import Config, OutputConfig, PersistCallback
from benchhud.demo import DemoSignal
from benchhud.devices.discovery import (
    SERIAL_BY_ID_DIR,
    CameraDevice,
    DeviceScanner,
    SystemDeviceScanner,
    can_capture,
    resolve_capture_device,
    same_device,
    stable_path,
)
from benchhud.devices.holders import held_by
from benchhud.instruments.owon_spm8104 import InstrumentLimits, open_device
from benchhud.instruments.session import InstrumentSession
from benchhud.instruments.signature import Signature, SignatureRecorder, compare, reference_at
from benchhud.instruments.signature import Verdict as SignatureVerdict
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.inventory import (
    HttpInventorySource,
    InventorySource,
    NullInventorySource,
    Part,
)
from benchhud.jobs.capture import CaptureContext
from benchhud.jobs.fault import FaultMonitor, format_fault_note
from benchhud.jobs.store import JobStore, StoredSignature, default_db_path
from benchhud.media import now_playing
from benchhud.media.now_playing import NowPlayingWatcher
from benchhud.output.feeds import fit_frame, thermal_frame, thermal_size
from benchhud.output.onair import Layer, compose_on_air, compose_preview, excluded
from benchhud.output.sink import OutputSink, open_output_sink
from benchhud.overlay.calibration import CalibrationError, Pair, find_tip, solve
from benchhud.overlay.composite import (
    MarkerTracker,
    OverlayMode,
    RangeTracker,
    colorize,
    compose,
)
from benchhud.overlay.graph import draw_graph_overlay
from benchhud.overlay.reference import compare as compare_reference
from benchhud.overlay.telemetry import Footprint, MeterReadout, draw_telemetry_overlay_sized
from benchhud.overlay.transform import RegistrationTransform
from benchhud.recon import (
    Engagement,
    HttpReconSource,
    NullReconSource,
    ReconSource,
    resolve_engagement,
)
from benchhud.shell.about import about_html
from benchhud.shell.board_dialogs import BoardSearchDialog, BoardsToSourceDialog
from benchhud.shell.composite_view import CompositeView
from benchhud.shell.icon import app_icon
from benchhud.shell.panel_manager import PanelManager
from benchhud.shell.panels.cameras import CamerasPanel
from benchhud.shell.panels.capture import CapturePanel
from benchhud.shell.panels.controls import ControlsPanel
from benchhud.shell.panels.engagement import EngagementPanel
from benchhud.shell.panels.flash import FlashPanel
from benchhud.shell.panels.interfaces import InterfacePanel
from benchhud.shell.panels.inventory import InventoryPanel
from benchhud.shell.panels.jobs import JobsPanel
from benchhud.shell.panels.launcher import LauncherPanel
from benchhud.shell.panels.minipro import MiniproPanel
from benchhud.shell.panels.pinout import PinoutPanel
from benchhud.shell.panels.power import PowerPanel
from benchhud.shell.panels.readings import ReadingsPanel
from benchhud.shell.panels.scope import ScopePanel
from benchhud.shell.panels.terminal import TerminalPanel, ViewFactory
from benchhud.shell.panels.thermal import ThermalPanel
from benchhud.shell.preferences import PreferencesDialog
from benchhud.shell.qt_util import unelide_tabs
from benchhud.shell.signature_image import render_signature
from benchhud.terminal import SUPPORTED as TERMINAL_SUPPORTED
from benchhud.terminal import TerminalHost, login_shell
from benchhud.terminal.serial_session import OwonConsoleSession, SerialSession
from benchhud.tickets import HttpTicketSource, NullTicketSource, TicketSource

# How often the frame loop runs with the virtual camera off. With it on, the
# loop runs at ``output.fps`` instead (#359).
_PREVIEW_FPS = 30
_MAX_FPS = 60


def frame_interval_ms(output: OutputConfig) -> int:
    """Milliseconds between frame ticks.

    With the output on, ``output.fps`` paces the whole loop: the readers only
    decode what a tick asks for, so a lower rate saves decode, compose and
    paint alike, and leaves that CPU for OBS on the same box.
    """
    fps = output.fps if output.enabled else _PREVIEW_FPS
    return round(1000 / min(max(int(fps), 1), _MAX_FPS))


# Bump when the default dock arrangement changes. The shell auto-saves the
# layout on close, so without this a layout from an older build would silently
# override a newer default (and the user never knowingly saved one).
LAYOUT_VERSION = 8

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
        if same_device(camera.identifier, text):
            return camera.label
    return text


def camera_holder_check(
    device: str, platform: str = sys.platform
) -> Callable[[], str | None] | None:
    """Names who holds ``device`` when it won't open (#325); None where we can't tell."""
    if not platform.startswith("linux") or not device.strip().startswith("/"):
        return None
    path = device.strip()
    return lambda: held_by(path)


def scope_source_for(
    device: str, cameras: Sequence[CameraDevice] = (), size: tuple[int, int] | None = None
) -> FallbackFrameSource:
    """The scope feed: the configured camera, or the synthetic pattern until it opens.

    A camera that is unplugged, busy (UVC allows one reader), not yet allowed by
    macOS, or simply wrong in the config must leave the shell usable — so we
    fall back rather than fail, keep retrying, and report why in ``status``.
    ``cameras`` is the current scan, used to resolve a Qt device id to an index.
    """
    fallback = SyntheticFrameSource(960, 540)
    if not device.strip():
        return FallbackFrameSource(None, fallback)
    resolved = resolve_capture_device(device, cameras)
    if not (size and size[0] > 0 and size[1] > 0) and isinstance(resolved, str):
        size = auto_size(probed_modes(resolved))  # not OpenCV's 640x480 (#342)
    camera = CameraFrameSource(resolved, size=size)
    return FallbackFrameSource(
        camera,
        fallback,
        name=camera_label(device, cameras),
        open_hint=camera_open_hint(),
        held_by=camera_holder_check(device),
    )


def thermal_source_for(device: str, cameras: Sequence[CameraDevice] = ()) -> FallbackFrameSource:
    """The thermal feed as °C arrays, with no test pattern when it's down.

    Unlike the scope, the thermal panel never shows a stand-in: a fake heatmap
    with a temperature on it could be taken for a real reading.
    """
    if not device.strip():
        return FallbackFrameSource(None, None)
    camera = Y16Camera(resolve_capture_device(device, cameras))
    # The T256s pauses while its shutter recalibrates, and crashes if it's
    # restarted mid-pause (#276): ride a pause out, then back off before reopening.
    return FallbackFrameSource(
        camera,
        None,
        name=camera_label(device, cameras),
        open_hint=camera_open_hint(),
        held_by=camera_holder_check(device),
        stall_timeout=THERMAL_STALL_S,
        retry_interval=THERMAL_RETRY_S,
        max_retry_interval=THERMAL_MAX_RETRY_S,
    )


#: How long the thermal may pause before it's given up on, and how long to wait
#: before reopening it, doubling to the cap while it keeps failing (#276).
THERMAL_STALL_S = 10.0
THERMAL_RETRY_S = 5.0
THERMAL_MAX_RETRY_S = 60.0
#: A paused feed is shown dimmed after this long.
THERMAL_PAUSED_S = 1.5
#: With a reset port configured, reset it once after the camera's been gone this long.
THERMAL_RESET_AFTER_S = 30.0


class MainWindow(QMainWindow):
    """Top-level HUD window."""

    #: A supply command finished on the instrument's thread: (what, its Future).
    #: A signal so the result is handled back on the GUI thread.
    control_done = Signal(str, object)

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
        self._preview_size: tuple[int, int] = (0, 0)
        self._thermal_pump: FramePump | None = None
        self._thermal_timer: QTimer | None = None
        self._thermal_source: FrameSource | None = None
        self._thermal_status: SourceStatus | None = None
        self._thermal_port_reset = False
        #: Whether the camera driver passes torn frames on (a seam for tests, #297).
        self.uvc_passes_torn_frames: Callable[[], bool] = passes_torn_frames
        self._uvc_warning: str | None = None
        #: Runs the port reset (a seam so tests don't call sudo).
        self.reset_runner: Callable[[list[str]], None] = self._run_reset
        self._thermal_device: str | None = None
        self._last_temps = None
        self._cameras: list[CameraDevice] = []
        self._sink: OutputSink | None = None
        # Composed frame size / scope frame size (#348); 1.0 = full size.
        self._compose_scale = 1.0
        # The scope-only / thermal-only feeds (#344), by name; Linux only.
        self._feed_sinks: dict[str, OutputSink] = {}
        self._feed_factory: SinkFactory | None = None
        self._thermal_feed_pending = False
        self._linux_output = sys.platform.startswith("linux")
        # The hub outlives server restarts, so a page reconnecting after a
        # Preferences change gets the current readings straight away.
        self._browser_hub = TopicHub()
        self._browser_server: BrowserSourceServer | None = None
        self._now_playing: NowPlayingWatcher | None = None
        self._browser_warning: str | None = None
        self._demo: DemoSignal | None = None
        self._demo_timer: QTimer | None = None
        self._demo_t = 0.0
        self._demo_scope = SyntheticFrameSource(960, 540)
        self._instrument_session: InstrumentSession | None = None
        self._instrument_timer: QTimer | None = None
        self._instrument_t = 0.0
        self._fault_monitor = FaultMonitor(self._config.faults.current_threshold_a)
        # Power-on signatures (#236): the latest telemetry time anchors a recording.
        self._signature_recorder = SignatureRecorder()
        self._last_signature: StoredSignature | None = None
        self._telemetry_t: float | None = None
        self._ticket_source: TicketSource = self._make_ticket_source()
        self._inventory_source: InventorySource = self._make_inventory_source()
        self._asset_source: AssetSource = self._make_asset_source()
        self._recon_source: ReconSource = self._make_recon_source()
        #: The engagement benchhud is working against (a ref only — the record
        #: and its sensitive detail stay in bench-recon). Set by "Import from
        #: engagement…"; the recon panels read the ref-safe projection live.
        self._active_engagement: str | None = None
        self._board_library = self._make_board_library()

        self._stabilise_camera_paths()
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
        self._wire_supply_controls()
        self._wire_signature()
        self.controls_panel = ControlsPanel(self._config)
        controls_dock = self.panels.add_panel(self.controls_panel, left)
        self.launcher_panel = LauncherPanel(self._config.tools)
        self.launcher_panel.terminal_tool_requested.connect(self.run_tool_in_terminal)
        # Asked before a CLI tool takes the terminal from a serial console. A
        # seam so tests needn't click a dialog: (current target) -> go ahead?
        self.confirm_terminal_switch = self._ask_terminal_switch
        launcher_dock = self.panels.add_panel(self.launcher_panel, left)
        self.tabifyDockWidget(controls_dock, launcher_dock)  # controls/tools tabs
        self.controls_panel.changed.connect(self.render_composite)
        # The thermal panel colours like the overlay: same range, same colormap.
        self.controls_panel.changed.connect(self._style_thermal_panel)
        self._style_thermal_panel()
        # Two-point thermal calibration (#243).
        self._calibration: list[Pair] | None = None
        self._thermal_range = RangeTracker()  # the overlay's auto range (#244)
        self._marker = MarkerTracker()  # a steady hot-spot marker (#271)
        # The known-good board's reference image for the selected job (#256).
        self._reference: np.ndarray | None = None
        self.choose_reference_file = self._ask_reference_file
        self.controls_panel.reference_capture_requested.connect(self.capture_reference)
        self.controls_panel.reference_load_requested.connect(self.load_reference)
        self._before_calibration: RegistrationTransform | None = None
        self.controls_panel.calibrate_requested.connect(self.start_calibration)
        self.controls_panel.undo_requested.connect(self.undo_calibration)
        self.composite_view.clicked.connect(self.calibration_click)
        cancel = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        cancel.activated.connect(self.cancel_calibration)
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
        self.terminal_panel.target_changed.connect(self.set_terminal_target)
        self._terminal_targets: list[tuple[str, str]] | None = None
        terminal_dock = self.panels.add_panel(self.terminal_panel, left)
        self.tabifyDockWidget(inventory_dock, terminal_dock)  # …and the terminal
        self.flash_panel = FlashPanel(
            self._config.flash, self._job_store, self.jobs_panel.selected_job_id
        )
        self.flash_panel.job_note_added.connect(self.jobs_panel.refresh_notes)
        flash_dock = self.panels.add_panel(self.flash_panel, left)
        self.tabifyDockWidget(terminal_dock, flash_dock)  # …and flashrom (#227)
        self.engagement_panel = EngagementPanel(
            lambda: self._recon_source,
            active_ref=lambda: self._active_engagement,
            on_activate=self.import_engagement,
        )
        engagement_dock = self.panels.add_panel(self.engagement_panel, left)
        self.tabifyDockWidget(terminal_dock, engagement_dock)  # …and the recon engagement (#305)
        self.interface_panel = InterfacePanel(
            lambda: self._asset_source,
            active_ref=lambda: self._active_engagement,
        )
        interface_dock = self.panels.add_panel(self.interface_panel, left)
        self.tabifyDockWidget(engagement_dock, interface_dock)  # …and the interface monitor (#307)
        self.pinout_panel = PinoutPanel(active_ref=lambda: self._active_engagement)
        pinout_dock = self.panels.add_panel(self.pinout_panel, left)
        self.tabifyDockWidget(interface_dock, pinout_dock)  # …and the pinout/protocol crib (#312)
        self.capture_panel = CapturePanel(
            lambda: self._recon_source,
            active_ref=lambda: self._active_engagement,
        )
        capture_dock = self.panels.add_panel(self.capture_panel, left)
        self.tabifyDockWidget(pinout_dock, capture_dock)  # …and the capture/dump status (#314)
        self.readings_panel = ReadingsPanel(self._job_store, self.jobs_panel.selected_job_id)
        self.jobs_panel.job_changed.connect(self.readings_panel.refresh)
        self.jobs_panel.job_changed.connect(self._reference_for_job)
        readings_dock = self.panels.add_panel(self.readings_panel, left)
        self.minipro_panel = MiniproPanel(
            self._config.minipro, self._job_store, self.jobs_panel.selected_job_id
        )
        self.minipro_panel.job_note_added.connect(self.jobs_panel.refresh_notes)
        minipro_dock = self.panels.add_panel(self.minipro_panel, left)
        self.tabifyDockWidget(flash_dock, minipro_dock)  # …and the T48 (#253)
        self.tabifyDockWidget(minipro_dock, readings_dock)  # …and readings (#235)
        self.cameras_panel = CamerasPanel(self._config.cameras, scanner=self._scanner)
        cameras_dock = self.panels.add_panel(self.cameras_panel, left)
        self.tabifyDockWidget(readings_dock, cameras_dock)  # …and aiming the cameras (#330)

        scope_dock.raise_()
        controls_dock.raise_()

        self._update_terminal_panel()

        self._build_boards_menu()
        self._build_view_menu()
        self._default_state = self.saveState()  # for "Reset layout"

        self._refresh_status()

        self._apply_config()
        unelide_tabs(self)

    def _stabilise_camera_paths(
        self,
        stable: Callable[[str], str | None] = stable_path,
        serial_stable: Callable[[str], str | None] | None = None,
    ) -> None:
        """Swap a configured /dev/videoN for its stable by-id link (#257).

        A camera that drops off USB can come back under another number; its
        by-id link follows it, so the scope and thermal reconnect by themselves.
        A path with no camera behind it right now is left alone.
        """
        if not sys.platform.startswith("linux"):
            return
        if serial_stable is None:

            def serial_stable(path: str) -> str | None:
                return stable_path(path, SERIAL_BY_ID_DIR)

        panels = self._config.panels
        port = panels.telemetry.port.strip()
        if port.startswith("/") and os.path.basename(port).startswith("tty"):
            link = serial_stable(port)
            if link:
                log.info("instrument port %s is now configured as %s", port, link)
                panels.telemetry.port = link
        for panel in (panels.scope, panels.thermal):
            device = panel.device.strip()
            if device.startswith("/") and os.path.basename(device).startswith("video"):
                link = stable(device)
                # Never onto a node with no picture: after a reboot an old number
                # can land on a camera's metadata node (#269).
                if link and can_capture(device) is not False:
                    log.info("camera %s is now configured by its stable name %s", device, link)
                    panel.device = link

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
            "cameras",
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
        self._feed_signature(snapshot, t)
        self.readings_panel.update_meter(snapshot.dmm if snapshot.ok else None)
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

    def _make_asset_source(self) -> AssetSource:
        """Build the asset source from the bench-parts (inventory) connection.

        Durable assets live on the same bench-parts service as consumable parts,
        so they share its base URL and token — there is no separate asset config.
        """
        inventory = self._config.inventory
        if inventory.base_url.strip():
            return HttpAssetSource(inventory.base_url, inventory.token)
        return NullAssetSource()

    def reconfigure_asset_source(self) -> None:
        """Rebuild the asset source after a Preferences change."""
        self._asset_source = self._make_asset_source()

    def _make_recon_source(self) -> ReconSource:
        """Build the recon source from config: bench-recon when set, else standalone."""
        recon = self._config.recon
        if recon.base_url.strip():
            return HttpReconSource(recon.base_url, recon.token)
        return NullReconSource()

    def reconfigure_recon_source(self) -> None:
        """Rebuild the recon source after a Preferences change."""
        self._recon_source = self._make_recon_source()

    @property
    def active_engagement(self) -> str | None:
        """The ref of the engagement benchhud is working against, if any."""
        return self._active_engagement

    def import_engagement(self, ref: str) -> Engagement | None:
        """Make ``ref`` the active engagement, if the source can resolve it.

        "Import from engagement…" — benchhud stores only the ref; the recon
        panels read the ref-safe projection live. A ref the source can't resolve
        leaves the active engagement unchanged and returns ``None``.
        """
        engagement = resolve_engagement(self._recon_source, ref)
        if engagement is not None:
            self._active_engagement = engagement.ref
        return engagement

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
        dialog.accepted.connect(self.reconfigure_frame_rate)
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

    # --- command-line tools (#225) ---------------------------------------------

    def run_tool_in_terminal(self, tool) -> None:
        """Open a command-line tool where it can be used.

        In the Terminal panel when it's running: the command goes at the shell
        prompt without Enter, so the operator adds arguments and runs it.
        Otherwise (the terminal is off, or on Windows) in a system terminal window.
        """
        cfg = self._config.terminal
        if not (TERMINAL_SUPPORTED and cfg.enabled and self._terminal is not None):
            self._run_tool_in_system_terminal(tool)
            return
        target = cfg.target or "shell"
        if target != "shell":
            if not self.confirm_terminal_switch(target):
                return
            self.set_terminal_target("shell", cfg.baud, cfg.line_ending, cfg.local_echo)
        if self._terminal is None:  # the switch failed to bring a shell up
            self._run_tool_in_system_terminal(tool)
            return
        self._terminal.write(f"{tool.command} ")
        dock = self.panels.dock("terminal")
        if dock is not None:
            dock.show()
            dock.raise_()
        self.terminal_panel.focus_terminal()

    def _run_tool_in_system_terminal(self, tool) -> None:
        try:
            self.launcher_panel.launcher.launch_in_system_terminal(tool)
        except Exception as exc:  # no terminal emulator, or it wouldn't start
            log.warning("couldn't open %s in a terminal: %s", tool.name, exc)
            self.statusBar().showMessage(
                f"Couldn't open {tool.name}: {exc}. Turn on the Terminal panel "
                "(Preferences → Terminal) to run command-line tools inside benchhud.",
                15000,
            )

    def _ask_terminal_switch(self, target: str) -> bool:
        label = "the OWON console" if target == "owon" else target
        answer = QMessageBox.question(
            self,
            "Switch the terminal?",
            f"The terminal is connected to {label}. Switch it to your shell to run "
            "this tool? That ends the serial session.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

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
        if scope is not None:
            # The good board goes under the thermal, so heat still reads on top (#256).
            scope = compare_reference(
                scope, self._reference, self.controls_panel.reference_settings(), time.monotonic()
            )
            self._publish_feed("scope", scope)
        # Compose no bigger than anything shows it (#348): the output and the
        # main view are usually well under the scope's 1080p, and every step
        # after this costs per pixel. Snapshots still use the full frame.
        full_size = (scope.shape[1], scope.shape[0]) if scope is not None else (0, 0)
        self._compose_scale = 1.0
        if scope is not None:
            k = self._compose_scale_for(full_size)
            if k < 1.0:
                small = (max(1, round(full_size[0] * k)), max(1, round(full_size[1] * k)))
                scope = cv2.resize(scope, small, interpolation=cv2.INTER_AREA)
                self._compose_scale = k
        if temps is None:
            self._show_composite(scope)
            return

        settings = self.controls_panel.current_settings()
        if settings.auto_range:
            low, high = self._thermal_range.update(temps)
            settings = replace(settings, tmin=low, tmax=high)
        thermal_sink = self._thermal_feed_sink(temps)
        if thermal_sink is not None:
            self._publish_feed(
                "thermal",
                thermal_frame(
                    temps,
                    settings.tmin,
                    settings.tmax,
                    settings.colormap,
                    thermal_sink.size,
                    scale_bar=settings.scale_bar,
                ),
            )
        if scope is None:
            # Thermal only — show it colourised on its own.
            self._show_composite(colorize(temps, settings.tmin, settings.tmax, settings.colormap))
            return
        transform = self.controls_panel.current_transform()
        if not transform.current:
            # A transform saved before #242 or #259: convert it to the current
            # placement now that both frame sizes are known. Same picture, new numbers.
            src = (temps.shape[1], temps.shape[0])
            transform = transform.migrated(src, full_size)
            self.controls_panel.set_transform(transform)
            log.info("thermal registration converted to the centred placement: %s", transform)
        marker = self._marker.update(temps) if settings.mode is OverlayMode.MARKER else None
        # The registration is in full-frame pixels; carry it to the smaller frame.
        placed = transform.for_scaled_frame(self._compose_scale)
        self._show_composite(compose(scope, temps, placed, settings, marker))

    def _compose_scale_for(self, scope_size: tuple[int, int]) -> float:
        """How far the scope can shrink before composing: to the larger of the
        virtual-camera output and the main view, never up. 1.0 = leave it."""
        width, height = scope_size
        if width <= 0 or height <= 0:
            return 1.0
        need = 0.0
        if self._sink is not None:
            out_w, out_h = self._sink.size
            need = max(out_w / width, out_h / height)
        if self.composite_view.isVisible():  # an unshown view has no real size
            view = self.composite_view.size()
            need = max(need, min(view.width() / width, view.height() / height))
        # Not worth a resize for the last few percent.
        return need if 0.0 < need < 0.9 else 1.0

    # --- thermal calibration (#243) --------------------------------------------

    def _style_thermal_panel(self) -> None:
        settings = self.controls_panel.current_settings()
        self.thermal_panel.set_style(
            auto_range=settings.auto_range,
            tmin=settings.tmin,
            tmax=settings.tmax,
            colormap=settings.colormap,
        )

    # --- known-good reference (#256) ---------------------------------------------

    def _selected_board(self) -> str:
        job_id = self.jobs_panel.selected_job_id()
        job = self._job_store.get_job(job_id) if job_id is not None else None
        return (job.board_id or "").strip() if job is not None else ""

    def _reference_for_job(self) -> None:
        """Load the selected job's board reference, if it has one."""
        board = self._selected_board()
        path = self._job_store.board_reference(board) if board else None
        image = cv2.imread(path) if path else None
        self._reference = image
        panel = self.controls_panel
        if not board:
            panel.show_reference("Select a job with a board number to use a reference.")
        elif image is None:
            panel.show_reference(f"No reference for {board} yet. Capture a good board.")
        else:
            panel.show_reference(f"Reference for {board} loaded.")
        self.render_composite()

    def _save_reference(self, image: np.ndarray, board: str) -> None:
        directory = self._job_store.references_dir()
        self._reference = image
        if directory is None:
            self.controls_panel.show_reference(f"Reference for {board} set (not saved).")
            return
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in board)
        path = directory / f"{safe}.png"
        cv2.imwrite(str(path), image)
        self._job_store.set_board_reference(board, str(path))
        self.controls_panel.show_reference(f"Saved as the known-good reference for {board}.")
        self.render_composite()

    def capture_reference(self) -> None:
        """This scope frame becomes the board's known-good reference."""
        board = self._selected_board()
        frame = self.scope_panel.last_frame
        if not board:
            self.controls_panel.show_reference(
                "Select a job with a board number first: references are per board.", error=True
            )
        elif frame is None:
            self.controls_panel.show_reference("No scope frame to capture yet.", error=True)
        else:
            self._save_reference(frame.copy(), board)

    def load_reference(self) -> None:
        board = self._selected_board()
        if not board:
            self.controls_panel.show_reference(
                "Select a job with a board number first: references are per board.", error=True
            )
            return
        path = self.choose_reference_file()
        if not path:
            return
        image = cv2.imread(path)
        if image is None:
            self.controls_panel.show_reference(f"Couldn't read {path} as an image.", error=True)
            return
        self._save_reference(image, board)

    def _ask_reference_file(self) -> str | None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "Known-good board photo", "", "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)"
        )
        return path or None

    @property
    def calibrating(self) -> bool:
        return self._calibration is not None

    def start_calibration(self) -> None:
        panel = self.controls_panel
        if self.scope_panel.last_frame is None or self.thermal_panel.last_temps is None:
            panel.show_calibration(
                "Calibration needs both cameras running: the scope and the thermal.",
                error=True,
            )
            return
        self._calibration = []
        self.composite_view.set_picking(True)
        panel.show_calibration(
            "1 of 2: touch the hot iron tip to the board, then click the tip in the "
            "main view. Esc cancels."
        )

    def calibration_click(self, x: float, y: float) -> None:
        if self._calibration is None:
            return
        panel = self.controls_panel
        temps = self.thermal_panel.last_temps
        scope = self.scope_panel.last_frame
        if temps is None or scope is None:
            self.cancel_calibration()
            return
        try:
            tip, celsius = find_tip(temps)
        except CalibrationError as exc:
            panel.show_calibration(str(exc), error=True)
            return
        # The click is on the composed frame, which may be smaller than the
        # scope's (#348); calibration works in the scope's own pixels.
        x, y = x / self._compose_scale, y / self._compose_scale
        self._calibration.append(Pair(tip, (x, y), celsius))
        if len(self._calibration) == 1:
            panel.show_calibration(
                f"Got the first spot ({celsius:.0f} °C). 2 of 2: move the tip somewhere "
                "far from it, then click the tip again."
            )
            return
        first, second = self._calibration
        src = (temps.shape[1], temps.shape[0])
        dst = (scope.shape[1], scope.shape[0])
        try:
            transform = solve(first, second, src, dst)
        except CalibrationError as exc:
            self._calibration = [first]  # keep the first spot, retry the second
            panel.show_calibration(str(exc), error=True)
            return
        self._before_calibration = panel.current_transform()
        self._finish_calibration()
        panel.set_transform(transform)
        panel.undo_button.setEnabled(True)
        panel.show_calibration(
            f"Calibrated: scale {transform.scale:.2f}, rotation "
            f"{transform.rotation_deg:.1f}°. Undo goes back."
        )

    def cancel_calibration(self) -> None:
        if self._calibration is None:
            return
        self._finish_calibration()
        self.controls_panel.show_calibration("")

    def _finish_calibration(self) -> None:
        self._calibration = None
        self.composite_view.set_picking(False)

    def undo_calibration(self) -> None:
        if self._before_calibration is None:
            return
        self.controls_panel.set_transform(self._before_calibration)
        self._before_calibration = None
        self.controls_panel.undo_button.setEnabled(False)
        self.controls_panel.show_calibration("Back to the previous alignment.")

    def _show_composite(self, frame, layers: Sequence[Layer] = ()) -> None:
        """Put one composed frame on screen and, if open, out to the sink.

        The telemetry overlay is applied here — the single point every composite
        path (scope-only, thermal-only, fused) flows through — so the on-screen
        preview and the virtual-cam output stay identical (WYSIWYG) *for the
        surfaces that may go on air*.

        ``layers`` are extra tagged surfaces offered to the compositor (recon
        panels, Track C). The split is the whole stream-safety guarantee: every
        layer is drawn onto the local preview, but :func:`compose_on_air` drops
        the sensitive ones from the frame handed to the sink, so a sensitive
        surface's bytes never reach the virtual camera. With no layers (the
        bench default today) preview and output are the same frame.
        """
        base = self._apply_overlays(frame)
        on_air = compose_on_air(base, layers)
        if excluded(layers):
            # Sensitive layers go on the preview only; copy first so drawing them
            # can never mutate the frame already chosen for the sink.
            preview = compose_preview(on_air.copy(), [ly for ly in layers if ly.sensitive])
            log.debug("kept off air: %s", ", ".join(excluded(layers)))
        else:
            preview = on_air
        self.composite_view.show_frame(preview)
        self._publish_frame(on_air)

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
                reference=self.power_panel.chart.reference,
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
        reasons = [getattr(self._sink, "reason", "")]
        reasons += [
            f"{name} feed: {getattr(sink, 'reason', '')}"
            for name, sink in self._feed_sinks.items()
            if getattr(sink, "reason", "")
        ]
        return "; ".join(r for r in reasons if r) or None

    def scope_status(self) -> SourceStatus | None:
        """Is the scope preview showing the camera, and if not, why (None: no preview)."""
        return getattr(self._scope_source, "status", None)

    def _scope_status_text(self) -> str:
        device = self._config.panels.scope.device
        label = camera_label(device, self._cameras) if device.strip() else "—"
        status = self.scope_status()
        if status is None or status.live:
            # Say when the camera didn't take the resolution asked for (#247).
            camera = getattr(self._scope_source, "camera", None)
            note = getattr(camera, "size_note", None)
            return f"{label} (⚠ {note})" if note else label
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
        if self._uvc_warning and self._thermal_pump is not None:
            suffix += f"   ·   ⚠ {self._uvc_warning}"
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
        scope = self._config.panels.scope
        device = scope.device
        size = (scope.width, scope.height)
        if source_factory is None:
            cameras = self._scan_cameras()
            self._cameras = cameras

            def source_factory() -> FrameSource:
                return scope_source_for(device, cameras, size)

        self._preview_device = device
        self._preview_size = size
        self._scope_source = source_factory()
        reader = LatestFrameReader(self._scope_source)
        self._pump = FramePump(reader, consumers=[self._on_scope_frame])
        self._pump.start()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._preview_tick)
        self._timer.start(frame_interval_ms(self._config.output))
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

    def reconfigure_frame_rate(self) -> None:
        """Repace the running frame timers after Preferences (#359)."""
        interval = frame_interval_ms(self._config.output)
        for timer in (self._timer, self._thermal_timer):
            if timer is not None and timer.interval() != interval:
                timer.setInterval(interval)

    def reconfigure_preview(self) -> None:
        """Reopen the scope after Preferences, if the chosen camera changed."""
        scope = self._config.panels.scope
        unchanged = (
            scope.device == self._preview_device
            and (scope.width, scope.height) == self._preview_size
        )
        if self._pump is None or unchanged:
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
        self._uvc_warning = None
        if self.uvc_passes_torn_frames():
            self._uvc_warning = TORN_FRAMES_WARNING
            log.warning("uvcvideo passes incomplete frames on (nodrop=1): the thermal may tear")
        self._thermal_source = source_factory()
        reader = LatestFrameReader(self._thermal_source)
        self._thermal_pump = FramePump(reader, consumers=[self._on_thermal_frame])
        self._thermal_pump.start()
        self._thermal_timer = QTimer(self)
        self._thermal_timer.timeout.connect(self._thermal_tick)
        self._thermal_timer.start(frame_interval_ms(self._config.output))
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
        source = self._thermal_source
        if source is not None and not self.demo_active:
            # A pause shows the last frame dimmed rather than blanking (#276).
            self.thermal_panel.set_paused(getattr(source, "stalled_for", 0.0) > THERMAL_PAUSED_S)
            self._maybe_reset_thermal_port(getattr(source, "lost_for", 0.0))
        status = self.thermal_status()
        if status != self._thermal_status:
            was_live = self._thermal_status is not None and self._thermal_status.live
            self._thermal_status = status
            if was_live and not self.demo_active:
                self.thermal_panel.clear()
                self.render_composite()
            self._refresh_status()

    def _maybe_reset_thermal_port(self, lost_for: float) -> None:
        """Reset the thermal's hub port once per outage, if one is configured (#276)."""
        if lost_for == 0.0:
            self._thermal_port_reset = False
            return
        spec = self._config.panels.thermal.reset_port.strip()
        if not spec or self._thermal_port_reset or lost_for < THERMAL_RESET_AFTER_S:
            return
        self._thermal_port_reset = True
        hub, _, port = spec.partition(":")
        argv = ["sudo", "-n", "uhubctl", "-l", hub, "-p", port, "-a", "cycle", "-d", "3"]
        log.warning("thermal gone %.0f s: resetting hub port %s", lost_for, spec)
        self.statusBar().showMessage(f"Thermal camera not answering: resetting its port ({spec})")
        threading.Thread(target=self.reset_runner, args=(argv,), daemon=True).start()

    @staticmethod
    def _run_reset(argv: list[str]) -> None:
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("port reset failed: %s", exc)
            return
        if done.returncode != 0:
            log.warning("port reset failed (%s): %s", done.returncode, done.stderr.strip())

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
        self._refresh_supply()

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
        self._refresh_supply()

    def reconfigure_instrument_session(self) -> None:
        """Rebuild the live session after a Preferences change."""
        self.stop_telemetry()
        self.start_telemetry()
        self._refresh_supply()

    # --- supply controls (#164) ---------------------------------------------

    def _wire_supply_controls(self) -> None:
        supply = self.power_panel.supply
        supply.set_voltage_requested.connect(
            lambda v: self.run_supply_command(f"Set {v:.2f} V", lambda d: d.apply_voltage(v))
        )
        supply.set_current_requested.connect(
            lambda a: self.run_supply_command(
                f"Set the limit to {a:.3f} A", lambda d: d.apply_current_limit(a)
            )
        )
        supply.output_on_requested.connect(
            lambda: self.run_supply_command("Output ON", lambda d: d.apply_output(True))
        )
        supply.output_off_requested.connect(
            lambda: self.run_supply_command("Output OFF", lambda d: d.apply_output(False))
        )
        supply.dmm_mode_requested.connect(
            lambda mode: self.run_supply_command(f"DMM to {mode}", lambda d: d.set_dmm_mode(mode))
        )
        self.control_done.connect(self._supply_command_done)
        self._refresh_supply()

    def _refresh_supply(self) -> None:
        """Enable the controls that may be used right now."""
        session = self._instrument_session
        instruments = self._config.instruments
        self.power_panel.supply.set_available(
            connected=session is not None and session.is_running,
            control=instruments.control,
            max_voltage=instruments.max_voltage,
            max_current=instruments.max_current,
        )

    def run_supply_command(self, what: str, action) -> None:
        """Run one supply command through the session, once.

        Never retried: a retried OUTP ON after a timeout is the electrical
        version of double-counting stock. The result, good or bad, is shown.
        """
        supply = self.power_panel.supply
        session = self._instrument_session
        if session is None or not session.is_running:
            supply.show_status(f"{what}: not done, no instrument is connected.", error=True)
            return
        supply.show_status(f"{what}…")
        future = session.submit(action)
        future.add_done_callback(lambda f: self.control_done.emit(what, f))

    # --- power-on signatures (#236) ------------------------------------------

    def _wire_signature(self) -> None:
        bar = self.power_panel.signature
        bar.record_requested.connect(self.start_signature)
        bar.mark_good_requested.connect(self.mark_signature_known_good)

    def _signature_references(self) -> tuple[str, list[Signature]]:
        """The selected job's board number and its known-good curves."""
        job_id = self.jobs_panel.selected_job_id()
        job = self._job_store.get_job(job_id) if job_id is not None else None
        board = (job.board_id or "").strip() if job is not None else ""
        stored = self._job_store.reference_signatures(board, exclude_job=job_id)
        return board, [Signature.from_json(s.points) for s in stored]

    def start_signature(self) -> None:
        """Record the current draw for the next few seconds, from now."""
        bar = self.power_panel.signature
        if self._telemetry_t is None:
            bar.show_status(
                "No supply readings to record. Turn on instrument telemetry first.", error=True
            )
            return
        self._signature_recorder.start(self._telemetry_t)
        _, references = self._signature_references()
        chart = self.power_panel.chart
        if references:
            window = self._signature_recorder.window
            steps = [i * 0.2 for i in range(int(window / 0.2) + 1)]
            chart.set_reference(
                [(t, reference_at(references, t)) for t in steps], self._telemetry_t
            )
        else:
            chart.clear_reference()
        bar.show_status(f"Recording the power-on for {self._signature_recorder.window:.0f} s…")

    def _feed_signature(self, snapshot: TelemetrySnapshot, t: float) -> None:
        self._telemetry_t = t
        if snapshot.voltage is None or snapshot.current is None:
            return
        recorder = self._signature_recorder
        if not recorder.recording:
            return
        signature = recorder.feed(t, snapshot.voltage, snapshot.current)
        if signature is None:
            self.power_panel.signature.show_status(
                f"Recording the power-on… {recorder.elapsed:.0f} / {recorder.window:.0f} s"
            )
            return
        self._signature_done(signature)

    def _signature_done(self, signature: Signature) -> None:
        bar = self.power_panel.signature
        board, references = self._signature_references()
        result = compare(signature, references)
        summary = (
            f"peak {signature.peak:.3f} A, settled {signature.settled:.3f} A over "
            f"{signature.duration:.0f} s"
        )
        flagged = result.verdict in (SignatureVerdict.LOW, SignatureVerdict.HIGH)
        job_id = self.jobs_panel.selected_job_id()
        if job_id is None:
            self._last_signature = None
            bar.good_button.setEnabled(False)
            bar.show_status(
                f"Power-on: {summary}, {result.describe()}. Select a job to keep it.",
                error=flagged,
            )
            return
        self._last_signature = self._job_store.add_signature(job_id, signature.to_json())
        attachment = self._write_signature_files(job_id, signature, references, board)
        self._job_store.add_note(
            job_id, f"Power-on signature: {summary}, {result.describe()}.", attachment=attachment
        )
        self.jobs_panel.refresh_notes()
        bar.good_button.setEnabled(True)
        bar.show_status(f"Power-on: {summary}, {result.describe()}.", error=flagged)

    def _write_signature_files(
        self, job_id: int, signature: Signature, references: list[Signature], board: str
    ) -> str | None:
        """The chart (PNG, the note's attachment) and the raw data (CSV) beside it."""
        directory = self._job_store.attachments_dir(job_id)
        if directory is None:
            return None
        stem = directory / f"power-on-{datetime.now():%Y%m%d-%H%M%S}"
        title = f"Power-on current, board {board}" if board else "Power-on current"
        render_signature(signature, references, title=title).save(str(stem) + ".png")
        (directory / (stem.name + ".csv")).write_text(signature.to_csv())
        return str(stem) + ".png"

    def mark_signature_known_good(self) -> None:
        bar = self.power_panel.signature
        stored = self._last_signature
        if stored is None:
            return
        job = self._job_store.get_job(stored.job_id)
        if job is None or not (job.board_id or "").strip():
            bar.show_status(
                "This job has no board number, so the curve can't be a reference yet.",
                error=True,
            )
            return
        self._job_store.set_signature_reference(stored.id, True)
        bar.show_status(f"That curve is now the known-good reference for board {job.board_id}.")

    def _supply_command_done(self, what: str, future) -> None:
        error = future.exception()
        supply = self.power_panel.supply
        if error is None:
            supply.show_status(f"{what}: done.")
            if what == "Output ON":
                self.start_signature()
        else:
            supply.show_status(f"{what}: not done. {error}", error=True)

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
        # The scope-only and thermal-only feeds (#344): each its own loopback
        # node, so Linux only. Elsewhere the one virtual camera is the composite.
        # The thermal one waits for its first frame, to take the camera's shape.
        self._feed_factory = factory
        if self._linux_output:
            if out.scope_device.strip():
                self._feed_sinks["scope"] = factory(out.scope_device.strip(), out.width, out.height)
            self._thermal_feed_pending = bool(out.thermal_device.strip())
        self._refresh_status()

    def _thermal_feed_sink(self, temps) -> OutputSink | None:
        """The thermal feed's sink, opened at the camera's shape on its first frame."""
        sink = self._feed_sinks.get("thermal")
        if sink is None and self._thermal_feed_pending and self._feed_factory is not None:
            self._thermal_feed_pending = False
            out = self._config.output
            size = thermal_size(out.height, temps.shape)
            sink = self._feed_factory(out.thermal_device.strip(), *size)
            self._feed_sinks["thermal"] = sink
            self._refresh_status()
        return sink

    def stop_output(self) -> None:
        """Close the virtual-camera sinks (idempotent)."""
        for sink in self._feed_sinks.values():
            sink.close()
        self._feed_sinks.clear()
        self._thermal_feed_pending = False
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

    def _publish_feed(self, name: str, frame) -> None:
        """Send one frame to the ``name`` feed (#344), fitted to it without stretching.

        A feed that fails is dropped on its own; the composite carries on.
        """
        sink = self._feed_sinks.get(name)
        if sink is None:
            return
        try:
            sink.send(fit_frame(frame, sink.size))
        except Exception:
            log.exception("%s feed failed; closing it", name)
            self._feed_sinks.pop(name, None)
            sink.close()

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
            self._now_playing = self._make_now_playing()
            server = BrowserSourceServer(
                self._browser_hub,
                cfg.port,
                panels=cfg.enabled,
                terminal=self._terminal,
                now_playing_art=self._now_playing.art if self._now_playing else None,
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
                if self._now_playing is not None:
                    self._now_playing.start()
        self._update_terminal_panel()
        self._refresh_status()

    def stop_browser_sources(self) -> None:
        """Stop serving and drop every connected page (idempotent). The shell keeps running."""
        self._browser_warning = None
        if self._now_playing is not None:
            self._now_playing.stop()
            self._now_playing = None
        if self._browser_server is None:
            return
        self._browser_server.stop()
        self._browser_server = None

    def _make_now_playing(self) -> NowPlayingWatcher | None:
        """The now-playing banner's watcher (#251), when it's on and can work here."""
        cfg = self._config.now_playing
        if not (now_playing.SUPPORTED and cfg.enabled and self._config.browser_sources.enabled):
            return None
        return NowPlayingWatcher(
            lambda body: self._browser_hub.publish("nowplaying", body),
            hide_when_paused=cfg.hide_when_paused,
            label=cfg.label,
        )

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
        argv, factory = self._terminal_target()
        if self._terminal is not None and self._terminal.argv != argv:
            self.stop_terminal()
        if self._terminal is None:
            kwargs = {}
            if self._terminal_session_factory is not None:
                kwargs["session_factory"] = self._terminal_session_factory
            elif factory is not None:
                kwargs["session_factory"] = factory
            self._terminal = TerminalHost(argv, share_token=cfg.share_token, **kwargs)
            self._terminal.start()
        self._terminal.share_token = cfg.share_token
        # A raw serial console is sensitive by default: its scrollback can carry
        # creds, keys or a dumped secret, so it is never put on the stream even if
        # sharing is ticked (#174). The shell and the query-only OWON console
        # stay operator-controlled. This is the terminal's Track D exclusion.
        self._terminal.set_sharing(cfg.share and not self._raw_serial_console())

    def _terminal_target(self):
        """``(identity, session factory)`` for what the terminal attaches to (#174).

        The identity is what :meth:`_sync_terminal` compares to decide whether to
        reconnect; the factory is None for the shell (a pty).
        """
        cfg = self._config.terminal
        target = cfg.target or "shell"
        owon_port = self._config.panels.telemetry.port
        if target == "shell":
            return login_shell(cfg.shell), None
        # Matched by device, not by spelling: /dev/ttyUSB0 and its by-id link are
        # the same OWON, and a second handle would interleave with the polling.
        if target == "owon" or (owon_port and same_device(target, owon_port)):
            # Never a second handle on the OWON: every line goes through the
            # running session, which also means reconnecting if it's replaced.
            session = self._instrument_session
            identity = ["owon:scpi", str(id(session))]
            return identity, lambda _argv, **kw: OwonConsoleSession(session, **kw)
        identity = [f"serial:{target}", str(cfg.baud), cfg.line_ending, str(cfg.local_echo)]
        return identity, lambda _argv, **kw: SerialSession(
            target,
            baud=cfg.baud,
            line_ending=cfg.line_ending,
            local_echo=cfg.local_echo,
            **kw,
        )

    def _raw_serial_console(self) -> bool:
        """Whether the terminal is attached to a raw serial port (a device under test).

        True for any serial target except the shell and the query-only OWON
        console — those are operator-owned and may be shared; a raw UART/serial
        console is sensitive and stays off air.
        """
        cfg = self._config.terminal
        target = cfg.target or "shell"
        if target in ("shell", "owon"):
            return False
        owon_port = self._config.panels.telemetry.port
        return not (owon_port and same_device(target, owon_port))

    def terminal_targets(self) -> list[tuple[str, str]]:
        """What the panel offers: the shell, the OWON console, and other serial ports."""
        targets = [("Shell", "shell")]
        owon_port = self._config.panels.telemetry.port
        if self._config.panels.telemetry.enabled:
            targets.append(("OWON SCPI console (queries only)", "owon"))
        try:
            ports = self._scanner.serial_ports()
        except Exception:
            ports = []
        for port in ports:
            if owon_port and same_device(port.device, owon_port):
                continue  # it's the OWON: reached through its session, above
            label = f"{port.device} — {port.description}" if port.description else port.device
            targets.append((label, port.device))
        return targets

    def set_terminal_target(self, target: str, baud: int, line_ending: str, echo: bool) -> None:
        """The panel's picker: attach the terminal to something else.

        The server restarts too, so the in-app page and any OBS source move to
        the new session rather than staying on the old one.
        """
        cfg = self._config.terminal
        owon_port = self._config.panels.telemetry.port
        if owon_port and same_device(target, owon_port):
            target = "owon"
        cfg.target, cfg.baud, cfg.line_ending, cfg.local_echo = target, baud, line_ending, echo
        self.restart_browser_sources()

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
            panel.show_message("The terminal isn't available on Windows yet.", attachable=False)
        elif not cfg.enabled:
            panel.show_message(
                "The terminal is off. Turn it on in Preferences → Terminal.", attachable=False
            )
        elif self._browser_warning:
            panel.show_message(f"The terminal can't start: {self._browser_warning}.")
        elif terminal is None or self._browser_server is None:
            panel.show_message("Starting the terminal…")
        else:
            panel.show_terminal(self._browser_server.terminal_url(terminal.write_token))
        state = terminal.state() if terminal is not None else None
        panel.set_off_air(self._raw_serial_console())
        panel.set_share_state(
            sharing=bool(state and state.sharing), paused=bool(state and state.paused)
        )
        if cfg.enabled and TERMINAL_SUPPORTED:
            targets = self.terminal_targets()
            if targets != self._terminal_targets or panel.target != (cfg.target or "shell"):
                self._terminal_targets = targets
                panel.set_targets(
                    targets,
                    cfg.target or "shell",
                    baud=cfg.baud,
                    line_ending=cfg.line_ending,
                    local_echo=cfg.local_echo,
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
                reference=self.power_panel.chart.reference,
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

    def childEvent(self, event: QChildEvent) -> None:
        """Keep tabbed docks' titles whole: Qt makes their tab bars on demand."""
        super().childEvent(event)
        if event.polished() and isinstance(event.child(), QTabBar):
            unelide_tabs(self)

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
