#!/usr/bin/env python3
"""Time MJPEG decode: full size against libjpeg's reduced-scale decode (#349).

Two steps, so the timing never depends on a camera being free:

    # 1. save compressed frames straight off a camera (no decode)
    bench_mjpeg_decode.py grab /dev/video4 1920x1080 frames-1080p/

    # 2. time decoding them
    bench_mjpeg_decode.py time frames-1080p/

``time`` with no directory uses a synthetic frame, which is fine for comparing
two builds on one machine but is not what a camera sends: quote camera frames.

CPU time is the process's (user + system), so a loaded machine doesn't inflate
it the way it inflates wall time. Needs only cv2 and numpy.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

_MJPG = cv2.VideoWriter_fourcc(*"MJPG")


def grab(device: str, size: str, out: Path, count: int) -> int:
    width, height = (int(n) for n in size.lower().split("x"))
    target: int | str = int(device) if device.isdigit() else device
    # V4L2 by name on Linux: OpenCV's FFmpeg backend ignores the size request.
    linux = sys.platform.startswith("linux")
    capture = cv2.VideoCapture(target, cv2.CAP_V4L2) if linux else cv2.VideoCapture(target)
    if not capture.isOpened():
        print(f"could not open {device}", file=sys.stderr)
        return 1
    capture.set(cv2.CAP_PROP_FOURCC, _MJPG)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    # FFmpeg hands over compressed frames with FORMAT=-1, V4L2 with CONVERT_RGB=0.
    raw = capture.set(cv2.CAP_PROP_FORMAT, -1) or capture.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    print(f"{capture.getBackendName()} backend, compressed frames accepted: {raw}")
    out.mkdir(parents=True, exist_ok=True)
    saved = 0
    for i in range(count + 15):
        ok, buf = capture.read()
        if not ok or buf is None:
            continue
        data = np.ascontiguousarray(buf).reshape(-1)
        if i == 0:
            print(f"first buffer: shape={buf.shape} dtype={buf.dtype} head={bytes(data[:4]).hex()}")
        if i < 15:  # let exposure settle
            continue
        if bytes(data[:2]) != b"\xff\xd8":
            print("not a JPEG buffer: this backend didn't hand over compressed frames")
            capture.release()
            return 1
        (out / f"frame-{saved:03d}.jpg").write_bytes(data.tobytes())
        saved += 1
    capture.release()
    print(f"saved {saved} frames to {out}")
    return 0


def _synthetic(width: int, height: int) -> list[np.ndarray]:
    rng = np.random.default_rng(349)
    small = rng.integers(0, 256, (height // 8, width // 8, 3), dtype=np.uint8)
    image = cv2.resize(small, (width, height), interpolation=cv2.INTER_CUBIC)
    noise = rng.integers(0, 24, image.shape, dtype=np.uint8)
    ok, data = cv2.imencode(".jpg", cv2.add(image, noise), [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return [data.reshape(-1)]


def _cpu_ms(fn, frames: list[np.ndarray], rounds: int) -> tuple[float, float]:
    """Median CPU and wall milliseconds per call over ``rounds`` passes."""
    cpu, wall = [], []
    for _ in range(rounds):
        for data in frames:
            c0, w0 = time.process_time(), time.perf_counter()
            fn(data)
            cpu.append((time.process_time() - c0) * 1e3)
            wall.append((time.perf_counter() - w0) * 1e3)
    return statistics.median(cpu), statistics.median(wall)


def timing(directory: Path | None, size: str, rounds: int) -> int:
    if directory is not None:
        frames = [np.fromfile(p, dtype=np.uint8) for p in sorted(directory.glob("*.jpg"))]
        if not frames:
            print(f"no .jpg frames in {directory}", file=sys.stderr)
            return 1
        source = str(directory)
    else:
        width, height = (int(n) for n in size.lower().split("x"))
        frames = _synthetic(width, height)
        source = f"synthetic {size}"
    full = cv2.imdecode(frames[0], cv2.IMREAD_COLOR)
    height, width = full.shape[:2]
    kb = statistics.mean(len(f) for f in frames) / 1024
    print(f"{source}: {len(frames)} frame(s), {width}x{height}, {kb:.0f} KiB average")
    print(f"OpenCV {cv2.__version__}, {cv2.getNumThreads()} threads, {rounds} passes")

    def full_then_resize(to: tuple[int, int]):
        return lambda d: cv2.resize(
            cv2.imdecode(d, cv2.IMREAD_COLOR), to, interpolation=cv2.INTER_AREA
        )

    cases = [
        ("full decode (today)", lambda d: cv2.imdecode(d, cv2.IMREAD_COLOR)),
        ("full decode + resize to 1/2", full_then_resize((width // 2, height // 2))),
        ("reduced 1/2", lambda d: cv2.imdecode(d, cv2.IMREAD_REDUCED_COLOR_2)),
        ("reduced 1/4", lambda d: cv2.imdecode(d, cv2.IMREAD_REDUCED_COLOR_4)),
    ]
    print(f"{'case':<30}{'size':>12}{'CPU ms':>10}{'wall ms':>10}")
    for name, fn in cases:
        shape = fn(frames[0]).shape
        cpu, wall = _cpu_ms(fn, frames, rounds)
        print(f"{name:<30}{f'{shape[1]}x{shape[0]}':>12}{cpu:>10.2f}{wall:>10.2f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("grab", help="save compressed frames from a camera")
    g.add_argument("device")
    g.add_argument("size", help="e.g. 1920x1080")
    g.add_argument("out", type=Path)
    g.add_argument("--count", type=int, default=30)
    t = sub.add_parser("time", help="time decoding saved frames")
    t.add_argument("directory", type=Path, nargs="?")
    t.add_argument("--size", default="1920x1080", help="synthetic frame size")
    t.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    if args.command == "grab":
        return grab(args.device, args.size, args.out, args.count)
    return timing(args.directory, args.size, args.rounds)


if __name__ == "__main__":
    raise SystemExit(main())
