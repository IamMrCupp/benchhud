"""Tests for the softcam (Windows) output sink.

There's no Windows box to verify against, so the contract with softcam's DLL is
pinned here instead: the frame buffer softcam's ``FrameBuffer::write`` memcpys
is tightly packed BGR24 in top-down row order, and the wrapper must hand it
exactly that. Those assertions are the whole reason this can be written on a Mac.
"""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.output.sink import OutputSink
from benchhud.output.softcam import (
    DIMENSION_MULTIPLE,
    REALTIME_FRAMERATE,
    SOFTCAM_BAD_SIZE,
    SOFTCAM_CAMERA_BUSY,
    SOFTCAM_DLL_MISSING,
    SoftcamSink,
    SoftcamUnavailable,
    frame_to_buffer,
    open_softcam_sink,
    validate_dimensions,
)


class _FakeCamera:
    """Stand-in for an opened softcam camera: records the bytes sent to it."""

    def __init__(self, width: int = 4, height: int = 4) -> None:
        self.size = (width, height)
        self.sends: list[bytes] = []
        self.close_count = 0
        self.is_connected = False

    def send(self, data: bytes) -> None:
        self.sends.append(data)

    def connected(self) -> bool:
        return self.is_connected

    def close(self) -> None:
        self.close_count += 1


def _sink_with_fake(width: int = 4, height: int = 4) -> tuple[SoftcamSink, _FakeCamera]:
    camera = _FakeCamera(width, height)
    sink = SoftcamSink(width, height, camera_factory=lambda w, h: camera)
    return sink, camera


def _bgr_frame(width: int, height: int) -> np.ndarray:
    """A frame whose every pixel is distinguishable, so layout errors show up."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        for x in range(width):
            frame[y, x] = (y * 16 + x, 100 + y, 200 + x)  # B, G, R
    return frame


# --- dimension constraints --------------------------------------------------


def test_dimensions_must_be_multiples_of_four() -> None:
    """softcam.h: width and height "should be ... a multiple of four".

    The receiver computes its DIB stride from these, so a bad size corrupts the
    picture rather than failing loudly. Reject it here instead.
    """
    validate_dimensions(1280, 720)  # the real capture size — must stay legal
    for width, height in ((640, 481), (639, 480), (2, 2)):
        with pytest.raises(ValueError, match="multiple of four"):
            validate_dimensions(width, height)


def test_dimensions_must_be_positive() -> None:
    for width, height in ((0, 480), (640, 0), (-4, 480)):
        with pytest.raises(ValueError, match="positive"):
            validate_dimensions(width, height)


def test_dimension_multiple_is_four() -> None:
    assert DIMENSION_MULTIPLE == 4


# --- the frame buffer contract ----------------------------------------------


def test_buffer_is_tightly_packed() -> None:
    """``FrameBuffer::write`` memcpys exactly 3*w*h bytes — no row padding."""
    frame = _bgr_frame(8, 4)
    assert len(frame_to_buffer(frame, (8, 4))) == 3 * 8 * 4


def test_buffer_keeps_bgr_order_unswapped() -> None:
    """softcam takes BGR24 natively — the one backend that converts nothing.

    v4l2 swaps BGR->RGB and cmio converts BGR->UYVY, so a copy-paste of either
    would silently invert this one's colors. Pin the byte order.
    """
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    frame[:, :] = (10, 20, 30)  # B=10, G=20, R=30
    assert frame_to_buffer(frame, (4, 4))[:3] == bytes((10, 20, 30))


def test_buffer_is_top_down() -> None:
    """The sender supplies top-down; softcam's transferToDIB does the flip.

    Its receiver reads source rows as ``h - 1 - y``, so flipping here too would
    double-flip and put the picture back upside down in OBS.
    """
    frame = _bgr_frame(4, 4)
    buffer = frame_to_buffer(frame, (4, 4))
    assert buffer[:3] == bytes(frame[0, 0])  # first row first
    assert buffer[-3:] == bytes(frame[3, 3])  # last row last


def test_buffer_handles_a_non_contiguous_frame() -> None:
    """Composited frames can arrive as views; ctypes needs real packed bytes."""
    view = _bgr_frame(8, 4)[:, ::-1]  # a flipped view — not C-contiguous
    assert not view.flags["C_CONTIGUOUS"]
    buffer = frame_to_buffer(view, (8, 4))
    assert len(buffer) == 3 * 8 * 4
    assert buffer[:3] == bytes(view[0, 0])


# --- the sink ---------------------------------------------------------------


def test_sink_satisfies_the_output_protocol() -> None:
    sink, _ = _sink_with_fake()
    assert isinstance(sink, OutputSink)


def test_sink_sends_the_frame_through() -> None:
    sink, camera = _sink_with_fake(4, 4)
    frame = _bgr_frame(4, 4)
    sink.send(frame)
    assert camera.sends == [frame_to_buffer(frame, (4, 4))]


def test_sink_rejects_a_mis_sized_frame() -> None:
    """A wrong size would desync the stream rather than fail visibly."""
    sink, camera = _sink_with_fake(4, 4)
    with pytest.raises(ValueError, match="sink expects"):
        sink.send(_bgr_frame(8, 4))
    assert camera.sends == []


def test_sink_rejects_a_non_bgr_frame() -> None:
    sink, _ = _sink_with_fake(4, 4)
    with pytest.raises(ValueError, match="3-channel"):
        sink.send(np.zeros((4, 4), dtype=np.uint8))


def test_sink_reports_its_size() -> None:
    sink, _ = _sink_with_fake(8, 4)
    assert sink.size == (8, 4)


def test_sink_close_releases_the_camera_once() -> None:
    sink, camera = _sink_with_fake()
    sink.close()
    sink.close()
    assert camera.close_count == 1


def test_sink_refuses_to_send_after_close() -> None:
    sink, _ = _sink_with_fake(4, 4)
    sink.close()
    with pytest.raises(RuntimeError, match="closed"):
        sink.send(_bgr_frame(4, 4))


def test_sink_exposes_whether_anything_is_watching() -> None:
    """scIsConnected is how the shell can tell OBS actually opened the camera."""
    sink, camera = _sink_with_fake()
    assert sink.connected is False
    camera.is_connected = True
    assert sink.connected is True


def test_sink_validates_dimensions_before_creating_the_camera() -> None:
    created: list[tuple[int, int]] = []

    def factory(width: int, height: int) -> _FakeCamera:
        created.append((width, height))
        return _FakeCamera(width, height)

    with pytest.raises(ValueError, match="multiple of four"):
        SoftcamSink(641, 480, camera_factory=factory)
    assert created == []


# --- opening, and the reasons it fails --------------------------------------


def test_realtime_framerate_is_zero() -> None:
    """Non-zero makes scSendFrame sleep inside the call to pace output.

    benchhud's source is real-time, so pacing there would stall the capture
    pipeline. softcam.h names 0 for exactly this case.
    """
    assert REALTIME_FRAMERATE == 0.0


def test_open_reports_a_missing_dll_as_actionable() -> None:
    def factory(width: int, height: int) -> _FakeCamera:
        raise OSError("softcam.dll not found")

    with pytest.raises(SoftcamUnavailable) as caught:
        open_softcam_sink(1280, 720, camera_factory=factory)
    assert caught.value.reason.startswith(SOFTCAM_DLL_MISSING)
    assert "softcam.dll not found" in caught.value.reason


def test_open_reports_an_already_held_camera_as_actionable() -> None:
    """scCreateCamera returns NULL when another instance already exists.

    Same single-writer situation as macOS, and it needs the same fix: go close
    the other sender. "Failed to create camera" would send people the wrong way.
    """

    def factory(width: int, height: int) -> _FakeCamera:
        raise RuntimeError("scCreateCamera returned NULL")

    with pytest.raises(SoftcamUnavailable) as caught:
        open_softcam_sink(1280, 720, camera_factory=factory)
    assert caught.value.reason.startswith(SOFTCAM_CAMERA_BUSY)
    assert "already" in caught.value.reason


def test_open_reports_an_illegal_configured_size_instead_of_raising() -> None:
    """The size comes from user config and start_output doesn't guard it.

    Letting the ValueError out would take the HUD down over a settings typo, so
    it falls back like every other failure — with a reason naming the setting.
    """
    with pytest.raises(SoftcamUnavailable) as caught:
        open_softcam_sink(1281, 720, camera_factory=lambda w, h: _FakeCamera(w, h))
    assert caught.value.reason.startswith(SOFTCAM_BAD_SIZE)


def test_open_returns_a_working_sink() -> None:
    camera = _FakeCamera(1280, 720)
    sink = open_softcam_sink(1280, 720, camera_factory=lambda w, h: camera)
    assert isinstance(sink, SoftcamSink)
    assert sink.size == (1280, 720)


# --- wiring into the shell-facing sink --------------------------------------


def test_open_output_sink_on_windows_reports_the_open_failure(monkeypatch) -> None:
    """The shell-facing fallback carries the same reason the open raised with."""
    from benchhud.output import sink as sink_module
    from benchhud.output import softcam

    monkeypatch.setattr(sink_module.sys, "platform", "win32")

    def _unavailable(*_a, **_k):
        raise SoftcamUnavailable("why it failed")

    monkeypatch.setattr(softcam, "open_softcam_sink", _unavailable)
    sink = sink_module.open_output_sink("/dev/ignored", 640, 480)
    assert isinstance(sink, sink_module.NullSink)
    assert sink.reason == "why it failed"


def test_open_output_sink_on_windows_returns_the_real_sink(monkeypatch) -> None:
    """Windows no longer falls through to the generic unsupported-platform path."""
    from benchhud.output import sink as sink_module
    from benchhud.output import softcam

    monkeypatch.setattr(sink_module.sys, "platform", "win32")
    monkeypatch.setattr(
        softcam,
        "open_softcam_sink",
        lambda w, h: SoftcamSink(w, h, camera_factory=lambda w2, h2: _FakeCamera(w2, h2)),
    )
    sink = sink_module.open_output_sink("/dev/ignored", 640, 480)
    assert isinstance(sink, SoftcamSink)
    assert not isinstance(sink, sink_module.NullSink)
    assert sink.size == (640, 480)


def test_unavailable_carries_its_reason() -> None:
    """The shell reads ``.reason``; ``str()`` must not diverge from it."""
    exc = SoftcamUnavailable("because")
    assert exc.reason == "because"
    assert str(exc) == "because"
