#!/usr/bin/env python3
"""Render the README's HUD pictures offscreen, from demo data (#500).

    QT_QPA_PLATFORM=offscreen PYTHONPATH=src python scripts/readme_pictures.py OUT_DIR

Writes four pictures into OUT_DIR:

- ``hud.png``: the whole window in demo mode.
- ``meter.png``: the Meter panel and its power graph, after a minute of
  demo readings.
- ``thermal-modes.jpg``: the four thermal overlay modes on the same warm chip.
- ``jobs.png``: the Jobs panel with a capture on the timeline, and the
  picture that capture saved.

Nothing comes from the bench. The window is the real ``MainWindow`` with demo
mode on, so the meter and power graph run on :class:`~benchhud.demo.DemoSignal`.
Only two things are swapped for demo mode's own stand-ins, because a moving bar
and a roaming blob don't show what the overlay is for: the scope frame is a
board drawn here, and the heat sits on the chips drawn on it. The job, its
notes and its capture are made up here too. The repository is public, so no
picture made by this script may carry anything from a real bench, job or
customer.

Look at every picture before committing it.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from benchhud import demo as demo_module
from benchhud.config import Config
from benchhud.jobs.store import JobStore
from benchhud.overlay.composite import OverlayMode
from benchhud.shell.main_window import MainWindow
from benchhud.shell.theme import apply_theme

SCOPE_SIZE = (1920, 1080)
THERMAL_SIZE = (256, 192)

# Where the drawn parts sit on the scope frame, in scope pixels.
CHIP = (880, 540, 230)  # centre x, centre y, side: the QFN that runs hot
COIL = (1340, 420, 150)  # the inductor beside it, warm
LDO = (520, 760, 90)  # a small regulator, a little warm


# --- the drawn board ------------------------------------------------------


def draw_board(width: int = SCOPE_SIZE[0], height: int = SCOPE_SIZE[1]) -> np.ndarray:
    """A made-up board as a microscope might see it: green mask, copper, parts."""
    rng = np.random.default_rng(500)
    mask = (52, 98, 36)  # BGR solder-mask green
    board = np.empty((height, width, 3), np.uint8)
    board[:] = mask
    trace = (70, 140, 60)
    copper = (95, 165, 205)
    silver = (205, 210, 212)
    silk = (235, 238, 236)

    # Traces under the mask, then vias.
    for y in range(120, height, 70):
        x0 = int(rng.integers(0, width // 3))
        cv2.polylines(
            board,
            [np.array([(x0, y), (x0 + 300, y), (x0 + 380, y + 60), (width, y + 60)], np.int32)],
            False,
            trace,
            10,
            cv2.LINE_AA,
        )
    for _ in range(60):
        x, y = int(rng.integers(40, width - 40)), int(rng.integers(40, height - 40))
        cv2.circle(board, (x, y), 13, copper, -1, cv2.LINE_AA)
        cv2.circle(board, (x, y), 6, (20, 30, 20), -1, cv2.LINE_AA)

    # The QFN: pads round the edge, the body, the marking, pin 1.
    cx, cy, side = CHIP
    half = side // 2
    for i in range(6):
        off = -half + 30 + i * (side - 60) // 5
        for px, py, w, h in (
            (cx + off, cy - half - 16, 18, 30),
            (cx + off, cy + half + 16, 18, 30),
            (cx - half - 16, cy + off, 30, 18),
            (cx + half + 16, cy + off, 30, 18),
        ):
            cv2.rectangle(board, (px - w // 2, py - h // 2), (px + w // 2, py + h // 2), silver, -1)
    cv2.rectangle(board, (cx - half, cy - half), (cx + half, cy + half), (38, 38, 40), -1)
    cv2.circle(board, (cx - half + 28, cy - half + 28), 10, (70, 70, 72), -1, cv2.LINE_AA)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(board, "X1=6A", (cx - 78, cy - 6), font, 1.5, (150, 150, 150), 3, cv2.LINE_AA)
    cv2.putText(board, "2347", (cx - 52, cy + 52), font, 1.2, (130, 130, 130), 2, cv2.LINE_AA)
    cv2.putText(board, "U7000", (cx - half, cy - half - 50), font, 1.3, silk, 3, cv2.LINE_AA)

    # The inductor.
    lx, ly, lside = COIL
    lh = lside // 2
    cv2.rectangle(board, (lx - lh - 18, ly - 40), (lx - lh + 10, ly + 40), silver, -1)
    cv2.rectangle(board, (lx + lh - 10, ly - 40), (lx + lh + 18, ly + 40), silver, -1)
    cv2.rectangle(board, (lx - lh, ly - lh), (lx + lh, ly + lh), (72, 74, 78), -1)
    cv2.putText(board, "4R7", (lx - 42, ly + 14), font, 1.3, (170, 170, 170), 3, cv2.LINE_AA)
    cv2.putText(board, "L3", (lx - 20, ly - lh - 24), font, 1.3, silk, 3, cv2.LINE_AA)

    # The small regulator, SOT-23-5.
    rx, ry, rside = LDO
    rh = rside // 2
    for leg in (rx - 30, rx, rx + 30):
        cv2.rectangle(board, (leg - 8, ry + rh), (leg + 8, ry + rh + 24), silver, -1)
    for leg in (rx - 30, rx + 30):
        cv2.rectangle(board, (leg - 8, ry - rh - 24), (leg + 8, ry - rh), silver, -1)
    cv2.rectangle(board, (rx - rh, ry - rh + 10), (rx + rh, ry + rh - 10), (40, 40, 42), -1)
    cv2.putText(board, "U12", (rx - 40, ry - rh - 40), font, 1.2, silk, 3, cv2.LINE_AA)

    # Passives: caps (tan) and resistors (black, with a code), each labelled.
    parts = [
        (640, 330, "C118", "cap"),
        (640, 430, "C122", "cap"),
        (1110, 300, "R20", "res"),
        (1110, 780, "C208", "cap"),
        (1340, 640, "C131", "cap"),
        (1540, 640, "R21", "res"),
        (300, 520, "R44", "res"),
        (760, 900, "C40", "cap"),
        (1600, 300, "C132", "cap"),
        (300, 300, "C17", "cap"),
    ]
    for px, py, label, kind in parts:
        cv2.rectangle(board, (px - 52, py - 22), (px - 30, py + 22), silver, -1)
        cv2.rectangle(board, (px + 30, py - 22), (px + 52, py + 22), silver, -1)
        body = (110, 150, 180) if kind == "cap" else (32, 32, 34)
        cv2.rectangle(board, (px - 30, py - 20), (px + 30, py + 20), body, -1)
        if kind == "res":
            cv2.putText(board, "103", (px - 22, py + 8), font, 0.6, (220, 220, 220), 1, cv2.LINE_AA)
        cv2.putText(board, label, (px - 40, py - 36), font, 1.0, silk, 2, cv2.LINE_AA)

    # A test point and a mounting hole.
    cv2.circle(board, (1720, 900), 26, copper, -1, cv2.LINE_AA)
    cv2.putText(board, "TP12", (1660, 860), font, 1.0, silk, 2, cv2.LINE_AA)
    cv2.circle(board, (120, 960), 70, copper, -1, cv2.LINE_AA)
    cv2.circle(board, (120, 960), 44, (18, 22, 18), -1, cv2.LINE_AA)

    # Microscope softness, sensor noise and a light falling off at the edges.
    board = cv2.GaussianBlur(board, (0, 0), 1.6)
    noise = rng.normal(0, 2, board.shape)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    r = np.hypot((xx - width / 2) / width, (yy - height / 2) / height)
    falloff = (1.0 - 0.45 * r**2)[..., None]
    return np.clip(board.astype(np.float32) * falloff + noise, 0, 255).astype(np.uint8)


def board_heat(
    width: int = THERMAL_SIZE[0], height: int = THERMAL_SIZE[1], tick: int = 0
) -> np.ndarray:
    """A °C field for the drawn board: the QFN hot, the inductor warm.

    Built in scope pixels and shrunk to the thermal sensor's size, so with the
    default registration (the thermal frame scaled to cover the scope frame and
    centred on it) each hot spot lands on its part.
    """
    sw, sh = SCOPE_SIZE
    scale = max(sw / width, sh / height)
    cover_w, cover_h = round(width * scale), round(height * scale)
    top, left = (cover_h - sh) / 2, (cover_w - sw) / 2
    yy, xx = np.mgrid[0:cover_h:4, 0:cover_w:4].astype(np.float32)
    xx -= left
    yy -= top
    field = np.full(xx.shape, 27.0, np.float32)
    breathe = 1.0 + 0.03 * np.sin(tick * 0.15)
    for (cx, cy, side), rise in ((CHIP, 44.0 * breathe), (COIL, 15.0), (LDO, 9.0)):
        sigma = side * 0.55
        field += rise * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma**2))
    field += np.random.default_rng(tick).normal(0, 0.25, field.shape).astype(np.float32)
    return cv2.resize(field, (width, height), interpolation=cv2.INTER_AREA)


class BoardSource:
    """A scope :class:`~benchhud.capture.frame_source.FrameSource` showing the drawn board."""

    def __init__(self) -> None:
        self._frame = draw_board()

    @property
    def size(self) -> tuple[int, int]:
        return SCOPE_SIZE

    def read(self) -> np.ndarray:
        return self._frame.copy()

    def close(self) -> None:
        pass


class BoardDemo(demo_module.DemoSignal):
    """Demo mode's signal with the heat on the drawn board, not roaming."""

    def thermal(self) -> np.ndarray:
        return board_heat(self.width, self.height, self._tick)


# --- the made-up job --------------------------------------------------------


def fake_job(store: JobStore) -> int:
    """One job with a few notes. Every word of it is made up."""
    job = store.create_job(
        "Example handheld, rev B",
        serial="DEMO-0001",
        customer="Demo customer",
        symptoms="No power. Draws 0.9 A at 12 V and gets hot by the buck controller.",
        external_ref="DEMO-1",
        board_id="EXAMPLE-BOARD-1",
    )
    for text in (
        "In for diagnosis. Lid off, no corrosion seen.",
        "Short on the standby rail, about 2 Ω to ground.",
        "Injected 1 V on the standby rail: U7000 warms up first.",
    ):
        store.add_note(job.id, text)
    return job.id


def stamps(start: str = "2026-11-20T15:02:00+00:00"):
    """A clock for the store that moves on a few minutes per write."""
    from datetime import datetime, timedelta

    now = datetime.fromisoformat(start)

    def tick() -> str:
        nonlocal now
        now += timedelta(minutes=7)
        return now.isoformat(timespec="seconds")

    return tick


# --- running the window -----------------------------------------------------


class NoDevices:
    """A scanner that finds nothing, so the window never opens a real camera."""

    def cameras(self):
        return []

    def serial_ports(self):
        return []


def settle(app: QApplication, seconds: float = 0.3) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def grab(widget: QWidget) -> QImage:
    image = widget.grab().toImage()
    image.setDevicePixelRatio(1)
    return image


def floated(app: QApplication, window: MainWindow, panel_id: str, width: int, height: int):
    """Float a panel's dock at a size that shows it whole, and return the panel."""
    dock = window.panels.dock(panel_id)
    dock.setFloating(True)
    dock.resize(width, height)
    dock.show()
    settle(app)
    return dock.widget()


def stacked(images: list[QImage], background: QColor) -> QImage:
    """Images one above the other, left edges aligned."""
    pad, gap = 24, 24
    out = QImage(
        pad * 2 + max(i.width() for i in images),
        pad * 2 + sum(i.height() for i in images) + gap * (len(images) - 1),
        QImage.Format.Format_RGB32,
    )
    out.fill(background)
    p = QPainter(out)
    y = pad
    for image in images:
        p.drawImage(pad, y, image)
        y += image.height() + gap
    p.end()
    return out


def sheet(tiles: list[tuple[QImage, str]], columns: int, background: QColor) -> QImage:
    """Tiles in a grid, each with its name above it."""
    pad, gap, caption = 24, 24, 44
    tw = max(t.width() for t, _ in tiles)
    th = max(t.height() for t, _ in tiles)
    rows = (len(tiles) + columns - 1) // columns
    out = QImage(
        pad * 2 + columns * tw + (columns - 1) * gap,
        pad * 2 + rows * (th + caption) + (rows - 1) * gap,
        QImage.Format.Format_RGB32,
    )
    out.fill(background)
    p = QPainter(out)
    font = p.font()
    font.setPixelSize(26)
    p.setFont(font)
    p.setPen(QColor(230, 230, 230))
    for i, (tile, name) in enumerate(tiles):
        x = pad + (i % columns) * (tw + gap)
        y = pad + (i // columns) * (th + caption + gap)
        p.drawText(QPoint(x, y + 30), name)
        p.drawImage(x, y + caption, tile)
    p.end()
    return out


def bgr_image(frame: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def main(out: Path) -> list[Path]:
    """Render the pictures into ``out`` and return their paths."""
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)

    home = Path(tempfile.mkdtemp(prefix="benchhud-readme-"))
    store = JobStore(home / "jobs.db", now=stamps())
    job_id = fake_job(store)

    config = Config()
    window = MainWindow(config, scanner=NoDevices(), job_store=store, themes_folder=home / "themes")
    window.resize(1600, 1200)
    window.show()
    settle(app)

    window.jobs_panel.refresh_jobs()
    window.jobs_panel.select_job(job_id)
    # The scope shows the drawn board; demo mode then fills in the heat and
    # the meter. With a preview already running, demo mode leaves the scope be.
    window.start_preview(source_factory=BoardSource)
    window.set_demo_mode(True)
    window._demo_timer.stop()  # driven by hand below, as fast as it will go
    window._demo = BoardDemo()
    for _ in range(300):  # a minute of demo readings at its 5 Hz
        window._demo_tick()
        app.processEvents()
    settle(app, 1.0)

    window.jobs_panel.capture_bench()
    settle(app)

    window.panels.dock("thermal").raise_()  # the raw thermal feed, top right
    settle(app)
    grab(window).save(str(out / "hud.png"))
    grab(floated(app, window, "power", 640, 1040)).save(str(out / "meter.png"))
    jobs = grab(floated(app, window, "jobs", 1000, 890))
    attachment = store.list_notes(job_id)[-1].attachment
    saved = QImage(attachment).scaledToWidth(jobs.width())
    background = window.palette().color(window.backgroundRole())
    stacked([jobs, saved], background).save(str(out / "jobs.png"))

    # The overlay modes, each through the window's own compose.
    names = {
        OverlayMode.BLEND: "Blend",
        OverlayMode.OUTLINE: "Isotherm outline",
        OverlayMode.TINT: "Threshold tint",
        OverlayMode.MARKER: "Marker",
    }
    controls = window.controls_panel
    tiles = []
    for mode, name in names.items():
        controls.mode_combo.setCurrentText(mode.value)
        window.render_composite()
        app.processEvents()
        frame = window.composite_view._frame
        tiles.append((bgr_image(frame).scaledToWidth(800), name))
    sheet(tiles, 2, background).save(str(out / "thermal-modes.jpg"), quality=90)

    # Not set_demo_mode(False): that starts polling the meter's serial port.
    window._stop_demo()
    window.close()
    store.close()
    shutil.rmtree(home, ignore_errors=True)
    return [out / name for name in ("hud.png", "meter.png", "jobs.png", "thermal-modes.jpg")]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    for path in main(Path(sys.argv[1])):
        print("wrote", path)
