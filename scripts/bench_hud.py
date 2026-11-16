#!/usr/bin/env python3
"""Run the HUD's frame pipeline headless and measure it (#390, for #351).

    uv run scripts/bench_hud.py                       # 20 s, the bench's settings
    uv run scripts/bench_hud.py --no-reduced-live     # full-size live decode
    uv run scripts/bench_hud.py --no-feeds --fps 30 --json after.json
    uv run scripts/bench_hud.py --compare before.json after.json

It builds the real ``MainWindow`` on Qt's offscreen platform and starts its
preview, thermal and output loops, so the code measured is the code the bench
runs. Only the three ends that need hardware are swapped:

- **Scope**: a 1080p MJPEG clip served at the camera's rate by a stand-in for
  ``cv2.VideoCapture``, behind the real ``CameraFrameSource``. The clip is
  generated in memory at start (seeded, so every run decodes the same bytes),
  or read from a directory of ``.jpg`` frames saved off a camera with
  ``bench_mjpeg_decode.py grab`` (``--clip``).
- **Thermal**: synthetic Y16 frames with a moving hot spot, behind the real
  ``Y16Camera``.
- **Output**: the real ``V4l2LoopbackSink``, writing to a device that throws
  the bytes away.

It reports output fps, CPU (whole process and per thread), time per pipeline
stage and memory, as a table and, with ``--json``, as a file two runs can be
compared from. Numbers are for comparing two builds or two settings on one
machine. They are not the bench's numbers unless they were taken on the bench.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import resource
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np

SCHEMA = 1
_MJPG = int.from_bytes(b"MJPG", "little")
_CAP_PROP_FRAME_WIDTH = 3
_CAP_PROP_FRAME_HEIGHT = 4
_CAP_PROP_FOURCC = 6
_CAP_PROP_FORMAT = 8


# --- the stand-in devices -----------------------------------------------------


def generate_clip(width: int, height: int, frames: int, quality: int = 85) -> list[np.ndarray]:
    """``frames`` JPEG buffers of a textured picture that moves a little each frame.

    The same recipe as ``bench_mjpeg_decode.py``'s synthetic frame, so the
    decode cost is in the same range: a blurred random texture plus per-pixel
    noise. Seeded: every run encodes the same pictures.
    """
    rng = np.random.default_rng(351)
    small = rng.integers(0, 256, (height // 8 + 8, width // 8 + 8, 3), dtype=np.uint8)
    big = cv2.resize(small, (width + 64, height + 64), interpolation=cv2.INTER_CUBIC)
    clip = []
    for i in range(frames):
        dx = round(32 + 30 * np.sin(2 * np.pi * i / frames))
        dy = round(32 + 30 * np.cos(2 * np.pi * i / frames))
        image = big[dy : dy + height, dx : dx + width]
        noise = rng.integers(0, 24, image.shape, dtype=np.uint8)
        ok, data = cv2.imencode(".jpg", cv2.add(image, noise), [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("could not encode the test clip")
        clip.append(data.reshape(-1))
    return clip


def load_clip(directory: Path) -> list[np.ndarray]:
    clip = [np.fromfile(p, dtype=np.uint8) for p in sorted(directory.glob("*.jpg"))]
    if not clip:
        raise SystemExit(f"no .jpg frames in {directory}")
    return clip


class _Paced:
    """Hands out one frame per period, blocking like a camera's ``read``."""

    def __init__(self, fps: float) -> None:
        self._period = 1.0 / fps
        self._due = time.perf_counter()
        #: Seconds spent waiting for the next frame: not work, so not timed.
        self.waited = 0.0
        self.index = 0

    def _wait(self) -> None:
        now = time.perf_counter()
        self._due += self._period
        if self._due > now:
            time.sleep(self._due - now)
            self.waited += time.perf_counter() - now  # what the sleep really took
        else:  # the reader fell behind: a camera drops the frames it missed
            self._due = now
        self.index += 1


class ClipCapture(_Paced):
    """The slice of ``cv2.VideoCapture`` the scope source uses, over a JPEG clip.

    Asked for compressed frames (``reduced_live``) it hands the JPEG over, as
    OpenCV's backends do. Otherwise ``retrieve`` decodes it at full size, which
    is what the V4L2 backend does for an MJPG camera.
    """

    def __init__(self, clip: list[np.ndarray], size: tuple[int, int], fps: float) -> None:
        super().__init__(fps)
        self._clip = clip
        self._size = size
        self._compressed = False
        self._open = True

    def isOpened(self) -> bool:
        return self._open

    def getBackendName(self) -> str:
        return "CLIP"

    def get(self, prop_id: int) -> float:
        return {
            _CAP_PROP_FRAME_WIDTH: self._size[0],
            _CAP_PROP_FRAME_HEIGHT: self._size[1],
            _CAP_PROP_FOURCC: _MJPG,
        }.get(prop_id, 0.0)

    def set(self, prop_id: int, value: float) -> bool:
        if prop_id == _CAP_PROP_FORMAT and value == -1:
            self._compressed = True
            return True
        return prop_id in (_CAP_PROP_FRAME_WIDTH, _CAP_PROP_FRAME_HEIGHT, _CAP_PROP_FOURCC)

    def grab(self) -> bool:
        if not self._open:
            return False
        self._wait()
        return True

    def retrieve(self) -> tuple[bool, Any]:
        data = self._clip[self.index % len(self._clip)]
        if self._compressed:
            return True, data
        frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return frame is not None, frame

    def read(self) -> tuple[bool, Any]:
        return self.retrieve() if self.grab() else (False, None)

    def release(self) -> None:
        self._open = False


class ThermalCapture(_Paced):
    """Raw Y16 frames (Kelvin x 64) with a hot spot that walks around the board."""

    def __init__(self, shape: tuple[int, int], fps: float) -> None:
        super().__init__(fps)
        self._shape = shape  # (rows, cols)
        rows, cols = shape
        self._yy, self._xx = np.mgrid[0:rows, 0:cols].astype(np.float32)
        rng = np.random.default_rng(352)
        self._ambient = 24.0 + rng.normal(0.0, 0.3, shape).astype(np.float32)
        self._open = True

    def isOpened(self) -> bool:
        return self._open

    def get(self, prop_id: int) -> float:
        rows, cols = self._shape
        return {_CAP_PROP_FRAME_WIDTH: cols, _CAP_PROP_FRAME_HEIGHT: rows}.get(prop_id, 0.0)

    def set(self, prop_id: int, value: float) -> bool:
        return True

    def read(self) -> tuple[bool, Any]:
        if not self._open:
            return False, None
        self._wait()
        rows, cols = self._shape
        phase = self.index / 120.0
        cx = cols * (0.5 + 0.3 * np.cos(phase))
        cy = rows * (0.5 + 0.3 * np.sin(phase))
        spot = 60.0 * np.exp(-((self._xx - cx) ** 2 + (self._yy - cy) ** 2) / (2 * 9.0**2))
        celsius = self._ambient + spot
        return True, np.rint((celsius + 273.15) * 64.0).astype(np.uint16)

    def release(self) -> None:
        self._open = False


class _DevNull:
    """A loopback node that takes the write and throws it away."""

    def __init__(self, pixelformat: int) -> None:
        self.pixelformat = pixelformat
        self._fd = os.open(os.devnull, os.O_WRONLY)

    def write(self, data) -> None:
        os.write(self._fd, data)

    def close(self) -> None:
        os.close(self._fd)


class CountingSink:
    """An output sink that counts the frames sent while a measurement is on."""

    def __init__(self, inner, clock: _Window) -> None:
        self._inner = inner
        self._clock = clock
        self.frames = 0

    @property
    def size(self) -> tuple[int, int]:
        return self._inner.size

    def send(self, frame: np.ndarray) -> None:
        self._inner.send(frame)
        if self._clock.on:
            self.frames += 1

    def close(self) -> None:
        self._inner.close()


class _NoDevices:
    """A scanner that finds nothing, so a run never touches a real camera or port."""

    def cameras(self) -> list:
        return []

    def serial_ports(self) -> list:
        return []


# --- measuring ----------------------------------------------------------------


class _Window:
    """Whether the measurement window is open. Warm-up frames aren't counted."""

    on = False


class Stages:
    """Wall time and call counts per named stage, taken by wrapping callables."""

    def __init__(self, clock: _Window) -> None:
        self._clock = clock
        self.totals: dict[str, list[float]] = {}
        self._undo: list[tuple[Any, str, Any, bool]] = []

    def add(self, name: str, seconds: float) -> None:
        if self._clock.on and name:
            entry = self.totals.setdefault(name, [0, 0.0])
            entry[0] += 1
            entry[1] += seconds

    def timed(self, fn: Callable, name: str | Callable[..., str]) -> Callable:
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                label = name if isinstance(name, str) else name(*args, **kwargs)
                self.add(label, time.perf_counter() - start)

        return wrapper

    def wrap(self, owner: Any, attr: str, name: str | Callable[..., str]) -> None:
        """Time ``owner.attr``. A missing attribute is an error: a stage that was
        renamed must fail the benchmark's test, not vanish from the table."""
        original = getattr(owner, attr)
        had_own = attr in getattr(owner, "__dict__", {})
        self._undo.append((owner, attr, original, had_own))
        setattr(owner, attr, self.timed(original, name))

    def restore(self) -> None:
        for owner, attr, original, had_own in reversed(self._undo):
            if had_own:
                setattr(owner, attr, original)
            else:
                delattr(owner, attr)
        self._undo.clear()


class ReaderProbe:
    """Times a camera's ``read`` on its reader thread, minus the wait for a frame."""

    def __init__(self, camera: Any, capture_of: Callable[[], _Paced | None], stages: Stages):
        self.native_id: int | None = None
        self.thread_cpu = 0.0
        self._thread_time = getattr(time, "thread_time", None)

        read = camera.read
        counts = hasattr(camera, "decodes")

        def timed_read():
            capture = capture_of()
            waited = capture.waited if capture is not None else 0.0
            before = camera.decodes if counts else -1
            start = time.perf_counter()
            frame = read()
            spent = time.perf_counter() - start
            if capture is not None:
                spent -= capture.waited - waited
            # A frame the decode gate skipped isn't a decode (#346).
            if frame is not None and (not counts or camera.decodes != before):
                stages.add(self.stage, max(spent, 0.0))
            self.native_id = threading.get_native_id()
            if self._thread_time is not None:
                self.thread_cpu = self._thread_time()
            return frame

        self.stage = ""
        camera.read = timed_read


def _proc_thread_cpu() -> dict[int, tuple[float, str]] | None:
    """CPU seconds and name per thread from /proc (Linux), else ``None``."""
    tasks = Path("/proc/self/task")
    if not tasks.is_dir():
        return None
    tick = os.sysconf("SC_CLK_TCK")
    out: dict[int, tuple[float, str]] = {}
    for task in tasks.iterdir():
        try:
            fields = (task / "stat").read_text().rsplit(")", 1)[1].split()
            name = (task / "comm").read_text().strip()
        except (OSError, IndexError):
            continue  # the thread ended between the listing and the read
        out[int(task.name)] = ((int(fields[11]) + int(fields[12])) / tick, name)
    return out


def _rss_mb() -> float | None:
    """Resident memory right now, where the OS says without a dependency (Linux)."""
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return pages * os.sysconf("SC_PAGE_SIZE") / 2**20


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if sys.platform == "darwin" else peak / 1024  # bytes there, KiB here


class _Snapshot:
    def __init__(self, probes: dict[str, ReaderProbe]) -> None:
        self.wall = time.perf_counter()
        self.process = time.process_time()
        thread_time = getattr(time, "thread_time", None)
        self.main = thread_time() if thread_time is not None else None
        self.readers = {name: probe.thread_cpu for name, probe in probes.items()}
        self.proc = _proc_thread_cpu()


def _thread_table(
    start: _Snapshot, end: _Snapshot, probes: dict[str, ReaderProbe]
) -> tuple[str, list[dict]]:
    """Per-thread CPU as percent of one core, busiest first, and where it came from."""
    wall = end.wall - start.wall
    total = end.process - start.process
    rows: dict[str, float] = {}
    if start.proc is not None and end.proc is not None:
        source = "procfs"
        roles = {probe.native_id: name for name, probe in probes.items()}
        roles[threading.main_thread().native_id] = "main (GUI, compose, output)"
        for tid, (cpu, comm) in end.proc.items():
            used = cpu - start.proc.get(tid, (0.0, ""))[0]
            name = roles.get(tid, f"other: {comm}")
            rows[name] = rows.get(name, 0.0) + used
    elif start.main is not None and end.main is not None:
        source = "thread_time"
        rows["main (GUI, compose, output)"] = end.main - start.main
        for name in probes:
            rows[name] = end.readers[name] - start.readers[name]
        # OpenCV's and Qt's own worker threads can't be read one by one here.
        rows["other threads (OpenCV, Qt)"] = max(0.0, total - sum(rows.values()))
    else:
        return "none", []
    table = [
        {"name": name, "cpu_pct": round(100 * cpu / wall, 1)}
        for name, cpu in sorted(rows.items(), key=lambda kv: -kv[1])
        if cpu / wall >= 0.0005 or not name.startswith("other")
    ]
    return source, table


#: The stage table, in pipeline order: (name, thread, depth in the tree).
_STAGE_ROWS = (
    ("scope decode", "scope reader", 0),
    ("thermal read + convert", "thermal reader", 0),
    ("scope tick", "main", 0),
    ("scope panel", "main", 1),
    ("render composite", "main", 1),
    ("scope feed", "main", 2),
    ("thermal feed colourise", "main", 2),
    ("thermal feed", "main", 2),
    ("compose (blend)", "main", 2),
    ("overlays", "main", 2),
    ("composite view", "main", 2),
    ("output (fit + send)", "main", 2),
    ("thermal tick", "main", 0),
    ("thermal panel", "main", 1),
)


# --- the run ------------------------------------------------------------------


def run(args: argparse.Namespace) -> dict:
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from benchhud import __version__
    from benchhud.capture.camera import CameraFrameSource
    from benchhud.capture.fallback import FallbackFrameSource
    from benchhud.capture.frame_source import SyntheticFrameSource
    from benchhud.capture.thermal import Y16Camera
    from benchhud.config import Config
    from benchhud.demo import DemoSignal
    from benchhud.jobs.store import JobStore
    from benchhud.output import v4l2
    from benchhud.shell import main_window
    from benchhud.shell.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])

    if args.clip is not None:
        clip = load_clip(args.clip)
        clip_source = str(args.clip)
    else:
        clip = generate_clip(*args.scope_size, args.clip_frames)
        clip_source = "generated"
    first = cv2.imdecode(clip[0], cv2.IMREAD_COLOR)
    if first is None:
        raise SystemExit("the clip's first frame is not a JPEG")
    scope_size = (first.shape[1], first.shape[0])

    config = Config()
    config.output.enabled = True
    config.output.device = "bench-composite"
    config.output.width, config.output.height = args.size
    config.output.fps = args.fps
    config.output.scope_device = "bench-scope" if args.feeds else ""
    config.output.thermal_device = "bench-thermal" if args.feeds and args.thermal else ""
    config.panels.scope.reduced_live = args.reduced_live
    config.telemetry_overlay.enabled = args.overlays
    config.graph_overlay.enabled = args.overlays
    config.shell.window_width, config.shell.window_height = args.window

    clock = _Window()
    stages = Stages(clock)
    probes: dict[str, ReaderProbe] = {}
    sinks: dict[str, CountingSink] = {}
    captures: dict[str, _Paced] = {}
    composite_format = v4l2.V4L2_PIX_FMT_RGB24 if args.rgb_node else v4l2.V4L2_PIX_FMT_BGR24

    def sink_factory(device: str, width: int, height: int) -> CountingSink:
        pixelformat = composite_format if device == "bench-composite" else v4l2.V4L2_PIX_FMT_BGR24
        inner = v4l2.V4l2LoopbackSink(
            device, width, height, device_factory=lambda d, w, h: _DevNull(pixelformat)
        )
        sinks[device] = CountingSink(inner, clock)
        return sinks[device]

    def scope_factory():
        def capture_factory(_device):
            captures["scope"] = ClipCapture(clip, scope_size, args.scope_fps)
            return captures["scope"]

        camera = CameraFrameSource(
            "bench-clip",
            size=scope_size,
            reduced_live=args.reduced_live,
            capture_factory=capture_factory,
        )
        probe = ReaderProbe(camera, lambda: captures.get("scope"), stages)
        probe.stage = "scope decode"
        probes["scope reader"] = probe
        return FallbackFrameSource(camera, SyntheticFrameSource(960, 540), name="clip")

    def thermal_factory():
        def capture_factory(_device):
            captures["thermal"] = ThermalCapture(args.thermal_shape, args.thermal_fps)
            return captures["thermal"]

        camera = Y16Camera("bench-thermal", capture_factory=capture_factory)
        probe = ReaderProbe(camera, lambda: captures.get("thermal"), stages)
        probe.stage = "thermal read + convert"
        probes["thermal reader"] = probe
        return FallbackFrameSource(camera, None, name="synthetic thermal")

    window = MainWindow(config, scanner=_NoDevices(), job_store=JobStore())
    window._linux_output = True  # the extra feeds are Linux-only in the app
    try:
        # Instance attributes shadow the methods, so the timers, the pumps and
        # render_composite all call the timed ones.
        stages.wrap(window, "_preview_tick", "scope tick")
        stages.wrap(window, "_thermal_tick", "thermal tick")
        stages.wrap(window, "render_composite", "render composite")
        # A feed with no sink returns at once: that isn't a stage, so no name.
        stages.wrap(
            window,
            "_publish_feed",
            lambda name, _frame: f"{name} feed" if name in window._feed_sinks else "",
        )
        stages.wrap(window, "_apply_overlays", "overlays")
        stages.wrap(window, "_publish_frame", "output (fit + send)")
        stages.wrap(window.scope_panel, "update_frame", "scope panel")
        stages.wrap(window.thermal_panel, "update_temps", "thermal panel")
        stages.wrap(window.composite_view, "show_frame", "composite view")
        stages.wrap(main_window, "compose", "compose (blend)")
        stages.wrap(main_window, "thermal_frame", "thermal feed colourise")

        window.show()
        window.start_preview(scope_factory)
        if args.thermal:
            window.start_thermal(thermal_factory)
        window.start_output(sink_factory)

        demo_timer = None
        if args.overlays:  # give the meter and the graph something to draw
            demo, t = DemoSignal(), [0.0]

            def telemetry() -> None:
                demo.step()
                t[0] += 0.2
                window.on_telemetry(demo.telemetry(), t[0])

            demo_timer = QTimer()
            demo_timer.timeout.connect(telemetry)
            demo_timer.start(200)

        snaps: list[_Snapshot] = []
        decodes: list[int] = []
        thermal_frames: list[int] = []
        scope_camera = window._scope_source.camera

        def mark() -> None:
            snaps.append(_Snapshot(probes))
            decodes.append(scope_camera.decodes)
            thermal_frames.append(captures["thermal"].index if "thermal" in captures else 0)

        def begin() -> None:
            mark()
            clock.on = True
            # Timed from here, so a slow start can't eat into the window.
            QTimer.singleShot(round(args.duration * 1000), finish)

        def finish() -> None:
            clock.on = False
            mark()
            loop.quit()

        # A local loop, not app.exec(): quitting the application would also end
        # whatever loop the caller (a test) runs next.
        loop = QEventLoop()
        QTimer.singleShot(round(args.warmup * 1000), begin)
        loop.exec()
        if demo_timer is not None:
            demo_timer.stop()
        if len(snaps) != 2:
            raise RuntimeError("the measurement window never closed")
        rss_now = _rss_mb()
    finally:
        window.close()
        stages.restore()
        app.processEvents()

    start, end = snaps
    wall = end.wall - start.wall
    thread_source, threads = _thread_table(start, end, probes)

    def fps(device: str) -> float | None:
        return round(sinks[device].frames / wall, 2) if device in sinks else None

    stage_rows = []
    known = {name for name, _thread, _depth in _STAGE_ROWS}
    extra = tuple((name, "main", 0) for name in stages.totals if name not in known)
    for name, thread, depth in _STAGE_ROWS + extra:
        if name not in stages.totals:
            continue
        calls, seconds = stages.totals[name]
        stage_rows.append(
            {
                "name": name,
                "thread": thread,
                "depth": depth,
                "calls": int(calls),
                "ms_per_call": round(1000 * seconds / calls, 3),
                "core_pct": round(100 * seconds / wall, 1),
            }
        )

    return {
        "schema": SCHEMA,
        "benchhud": __version__,
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
            "opencv": cv2.__version__,
            "opencv_threads": cv2.getNumThreads(),
            "pyside6": pyside_version,
        },
        "config": {
            "output_size": list(args.size),
            "output_fps": args.fps,
            "reduced_live": args.reduced_live,
            "feeds": args.feeds,
            "thermal": args.thermal,
            "overlays": args.overlays,
            "rgb_node": args.rgb_node,
            "window": list(args.window),
            "duration_s": round(wall, 2),
            "warmup_s": args.warmup,
        },
        "clip": {
            "source": clip_source,
            "frames": len(clip),
            "size": list(scope_size),
            "fps": args.scope_fps,
            "mean_kib": round(sum(len(f) for f in clip) / len(clip) / 1024, 1),
            "thermal_shape": list(args.thermal_shape) if args.thermal else None,
            "thermal_fps": args.thermal_fps if args.thermal else None,
        },
        "fps": {
            "composite": fps("bench-composite"),
            "scope_feed": fps("bench-scope"),
            "thermal_feed": fps("bench-thermal"),
            "scope_decoded": round((decodes[1] - decodes[0]) / wall, 2),
            "thermal_in": round((thermal_frames[1] - thermal_frames[0]) / wall, 2)
            if args.thermal
            else None,
        },
        "cpu": {
            "total_pct": round(100 * (end.process - start.process) / wall, 1),
            "per_thread_source": thread_source,
            "threads": threads,
        },
        "stages": stage_rows,
        "memory": {
            "peak_rss_mb": round(_peak_rss_mb(), 1),
            "rss_mb": round(rss_now, 1) if rss_now is not None else None,
        },
    }


# --- reporting ----------------------------------------------------------------


def _on(value: bool) -> str:
    return "on" if value else "off"


def format_result(result: dict) -> str:
    host, config, clip = result["host"], result["config"], result["clip"]
    fps, cpu, memory = result["fps"], result["cpu"], result["memory"]
    width, height = config["output_size"]
    lines = [
        f"benchhud {result['benchhud']} on {host['platform']} ({host['cpus']} CPUs), "
        f"Python {host['python']}, OpenCV {host['opencv']} ({host['opencv_threads']} threads), "
        f"PySide6 {host['pyside6']}",
        f"scope   {clip['source']} clip, {clip['frames']} frames of "
        f"{clip['size'][0]}x{clip['size'][1]} MJPEG at {clip['fps']:g} fps, "
        f"{clip['mean_kib']:.0f} KiB a frame",
    ]
    if config["thermal"]:
        rows, cols = clip["thermal_shape"]
        lines.append(f"thermal synthetic Y16, {cols}x{rows} at {clip['thermal_fps']:g} fps")
    else:
        lines.append("thermal off")
    lines += [
        f"config  output {width}x{height} capped at {config['output_fps']} fps, "
        f"reduced_live {_on(config['reduced_live'])}, extra feeds {_on(config['feeds'])}, "
        f"overlays {_on(config['overlays'])}, "
        f"composite node {'RGB3' if config['rgb_node'] else 'BGR3'}",
        f"window  {config['duration_s']:g} s measured after {config['warmup_s']:g} s warm-up",
        "",
        f"{'composite out':<26}{fps['composite']:>8.2f} fps",
    ]
    for label, key in (
        ("scope feed out", "scope_feed"),
        ("thermal feed out", "thermal_feed"),
        ("scope frames decoded", "scope_decoded"),
        ("thermal frames in", "thermal_in"),
    ):
        if fps[key] is not None:
            lines.append(f"{label:<26}{fps[key]:>8.2f} fps")
    lines.append(f"{'CPU, whole process':<26}{cpu['total_pct']:>8.1f} % of one core")
    lines.append(f"{'peak RSS':<26}{memory['peak_rss_mb']:>8.1f} MB")
    if memory["rss_mb"] is not None:
        lines.append(f"{'RSS at the end':<26}{memory['rss_mb']:>8.1f} MB")
    lines += ["", f"{'thread':<34}{'CPU %':>8}"]
    lines += [f"{row['name']:<34}{row['cpu_pct']:>8.1f}" for row in cpu["threads"]]
    if not cpu["threads"]:
        lines.append("(no per-thread clock on this platform)")
    lines += ["", f"{'stage':<34}{'thread':<16}{'calls':>7}{'ms/call':>10}{'% core':>8}"]
    for row in result["stages"]:
        name = "  " * row["depth"] + row["name"]
        lines.append(
            f"{name:<34}{row['thread']:<16}{row['calls']:>7}"
            f"{row['ms_per_call']:>10.3f}{row['core_pct']:>8.1f}"
        )
    return "\n".join(lines)


def format_comparison(before: dict, after: dict) -> str:
    """Two results side by side, with the change."""
    lines = []
    for key in ("host", "clip"):
        if before[key] != after[key]:
            lines.append(f"note: the two runs differ in {key}; the numbers may not be comparable")
    ours, theirs = before["config"], after["config"]
    changed = [k for k in ours if k != "duration_s" and ours[k] != theirs.get(k)]
    if changed:
        lines.append(
            "config differs: " + ", ".join(f"{k} {ours[k]} -> {theirs.get(k)}" for k in changed)
        )
    lines.append(f"{'':<36}{'before':>10}{'after':>10}{'change':>10}")

    def row(label: str, a: float | None, b: float | None, unit: str) -> None:
        if a is None and b is None:
            return
        shown = [f"{v:>10.2f}" if v is not None else f"{'-':>10}" for v in (a, b)]
        delta = f"{b - a:>+10.2f}" if a is not None and b is not None else f"{'':>10}"
        lines.append(f"{label:<36}{shown[0]}{shown[1]}{delta} {unit}")

    for key, label in (
        ("composite", "composite out"),
        ("scope_feed", "scope feed out"),
        ("thermal_feed", "thermal feed out"),
        ("scope_decoded", "scope frames decoded"),
    ):
        row(label, before["fps"].get(key), after["fps"].get(key), "fps")
    row("CPU, whole process", before["cpu"]["total_pct"], after["cpu"]["total_pct"], "% core")
    for name, a, b in _paired(before["cpu"]["threads"], after["cpu"]["threads"], "cpu_pct"):
        row(f"  {name}", a, b, "% core")
    row("peak RSS", before["memory"]["peak_rss_mb"], after["memory"]["peak_rss_mb"], "MB")
    row("RSS at the end", before["memory"]["rss_mb"], after["memory"]["rss_mb"], "MB")
    lines.append("stages, ms per call:")
    for name, a, b in _paired(before["stages"], after["stages"], "ms_per_call"):
        row(f"  {name}", a, b, "ms")
    return "\n".join(lines)


def _paired(before: list[dict], after: list[dict], field: str):
    theirs = {row["name"]: row[field] for row in after}
    seen = set()
    for row in before:
        seen.add(row["name"])
        yield row["name"], row[field], theirs.get(row["name"])
    for row in after:
        if row["name"] not in seen:
            yield row["name"], None, row[field]


# --- command line -------------------------------------------------------------


def _pair(text: str) -> tuple[int, int]:
    try:
        a, b = (int(n) for n in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {text!r}") from None
    if a <= 0 or b <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive size, got {text!r}")
    return a, b


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the HUD's frame pipeline headless and measure it."
    )
    flag = argparse.BooleanOptionalAction
    parser.add_argument("--duration", type=float, default=20.0, help="seconds measured (20)")
    parser.add_argument("--warmup", type=float, default=3.0, help="seconds run first (3)")
    parser.add_argument("--size", type=_pair, default=(1280, 720), help="output size (1280x720)")
    parser.add_argument("--fps", type=int, default=15, help="output.fps, the frame cap (15)")
    parser.add_argument(
        "--reduced-live",
        action=flag,
        default=True,
        help="panels.scope.reduced_live: decode the live scope frame small (on)",
    )
    parser.add_argument(
        "--feeds", action=flag, default=True, help="the scope-only and thermal-only feeds (on)"
    )
    parser.add_argument("--thermal", action=flag, default=True, help="the thermal camera (on)")
    parser.add_argument(
        "--overlays",
        action=flag,
        default=False,
        help="the meter and power-graph overlays, fed demo telemetry (off)",
    )
    parser.add_argument(
        "--rgb-node",
        action="store_true",
        help="the composite node answers RGB3, as one a reader already holds does",
    )
    parser.add_argument(
        "--clip", type=Path, help="a directory of .jpg frames off a camera, instead of generating"
    )
    parser.add_argument(
        "--scope-size", type=_pair, default=(1920, 1080), help="generated clip size (1920x1080)"
    )
    parser.add_argument("--scope-fps", type=float, default=30.0, help="scope camera rate (30)")
    parser.add_argument(
        "--clip-frames", type=int, default=30, help="frames in the generated clip (30)"
    )
    parser.add_argument(
        "--thermal-shape",
        type=_pair,
        default=(256, 192),
        metavar="ROWSxCOLS",
        help="thermal array shape, rows x columns (256x192, the bench's portrait camera)",
    )
    parser.add_argument("--thermal-fps", type=float, default=25.0, help="thermal camera rate (25)")
    parser.add_argument(
        "--window", type=_pair, default=(1200, 760), help="HUD window size (1200x760)"
    )
    parser.add_argument("--json", type=Path, help="also write the result here as JSON")
    parser.add_argument(
        "--compare",
        nargs=2,
        type=Path,
        metavar=("BEFORE", "AFTER"),
        help="compare two saved results and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.compare is not None:
        before, after = (json.loads(path.read_text()) for path in args.compare)
        print(format_comparison(before, after))
        return 0
    # A run must not read or write the user's config or jobs database.
    with contextlib.ExitStack() as stack:
        if "BENCHHUD_CONFIG" not in os.environ:
            import tempfile

            scratch = stack.enter_context(tempfile.TemporaryDirectory(prefix="bench_hud-"))
            os.environ["BENCHHUD_CONFIG"] = str(Path(scratch) / "config.toml")
            stack.callback(os.environ.pop, "BENCHHUD_CONFIG", None)
        result = run(args)
    print(format_result(result))
    if args.json is not None:
        args.json.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
