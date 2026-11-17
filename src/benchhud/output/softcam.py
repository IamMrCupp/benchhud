"""Write composited frames to a softcam virtual camera (Windows).

This is the Windows virtual-camera output: softcam is a DirectShow filter DLL
(MIT) that registers a camera-like device, and its Sender API lets a process
push arbitrary images into it. Any DirectShow consumer — OBS included — sees a
camera. Only that DLL's five-function C API is used here, through ``ctypes``,
so nothing GPL is linked and benchhud's licensing stays open.

The DLL only exists on Windows, so the layout rules are pure functions tested
byte-for-byte on any OS, and :class:`SoftcamSink` takes an injectable
``camera_factory`` — frame handling is unit-tested with a fake camera. The real
``scCreateCamera`` runs in CI's ``softcam`` job, which builds the DLL, registers
it and reads the frames back through DirectShow (``tests/test_softcam_windows.py``).

**The DLL that gets loaded is the one Windows has registered.** A camera app
finds softcam through its COM registration, which records the DLL's full path;
the sender has to be that same build, because the two sides share a memory
layout. Sending works without the registration (it only creates shared memory),
so an unregistered DLL would take frames that no app could ever see. The sink
therefore refuses to open until the registration is there, and says how to add
it. :mod:`benchhud.output.softcam_register` does the registering.

**The frame buffer is BGR24, top-down, tightly packed** — read out of softcam's
implementation, since neither its header nor its README says so. Its sender-side
``FrameBuffer::write`` memcpys exactly ``3 * width * height`` bytes, and its
receiver-side ``transferToDIB`` is what reverses the rows (``h - 1 - y``) and
adds the 4-byte DIB alignment. Softcam does the bottom-up flip itself, and its
output type is ``BI_RGB`` / ``MEDIASUBTYPE_RGB24`` — physically blue-green-red
on Windows — so the bytes we hand it are BGR. That's OpenCV's native layout,
which makes this the one backend that converts nothing: ``v4l2.py`` swaps
BGR->RGB and ``cmio.py`` converts BGR->UYVY.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

BYTES_PER_PIXEL = 3

#: ``softcam.h``: width and height "should be a positive number and be a
#: multiple of four". The receiver derives its DIB stride from them, so a bad
#: size skews the picture instead of failing — hence the check on our side.
DIMENSION_MULTIPLE = 4

#: Framerate passed to ``scCreateCamera``. At any non-zero value ``scSendFrame``
#: sleeps inside the call to pace output; benchhud's source is real-time, so
#: that would stall the capture pipeline. ``softcam.h`` names 0 for this case:
#: "useful if the application has a real-time source stream".
REALTIME_FRAMERATE = 0.0

#: The camera name softcam's DLL registers (``FILTER_NAME`` in its
#: ``softcam.cpp``): what OBS lists under Video Capture Device.
SOFTCAM_DEVICE_NAME = "DirectShow Softcam"

#: softcam's COM class id (``CLSID_DShowSoftcam`` in its ``softcam.cpp``). Its
#: ``InprocServer32`` key holds the registered DLL's full path.
SOFTCAM_CLSID = "{AEF3B972-5FA5-4647-9571-358EB472BC9E}"

SOFTCAM_DLL_NAME = "softcam.dll"

#: Points at a softcam.dll to register, ahead of the one shipped with benchhud.
SOFTCAM_DLL_ENV = "BENCHHUD_SOFTCAM_DLL"


def registered_dll_path() -> Path | None:
    """The softcam DLL registered with Windows, or ``None`` if there isn't one.

    Reads the 64-bit registry view: benchhud and OBS are both 64-bit, and a
    32-bit registration is a different DLL that neither of them can load.
    """
    try:
        import winreg
    except ImportError:  # not Windows
        return None
    key_path = rf"CLSID\{SOFTCAM_CLSID}\InprocServer32"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, key_path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "")
    except OSError:
        return None
    return Path(value) if value else None


def bundled_dll_path() -> Path | None:
    """The softcam DLL that came with this benchhud, or ``None``.

    The installer puts it beside the ``benchhud`` package (Briefcase's ``app``
    folder). A source checkout has it where ``packaging/windows/build-softcam.ps1``
    leaves it. :data:`SOFTCAM_DLL_ENV` names another one.
    """
    package_parent = Path(__file__).resolve().parents[2]
    candidates = [
        package_parent / SOFTCAM_DLL_NAME,
        package_parent.parent / "packaging" / "windows" / "softcam" / SOFTCAM_DLL_NAME,
    ]
    override = os.environ.get(SOFTCAM_DLL_ENV)
    if override:
        candidates.insert(0, Path(override))
    return next((path for path in candidates if path.is_file()), None)


def validate_dimensions(width: int, height: int) -> None:
    """Check a frame size against softcam's documented constraints."""
    if width <= 0 or height <= 0:
        raise ValueError(f"camera size must be positive, got {width}x{height}")
    if width % DIMENSION_MULTIPLE or height % DIMENSION_MULTIPLE:
        raise ValueError(f"camera size must be a multiple of four, got {width}x{height}")


def frame_to_buffer(frame: np.ndarray, size: tuple[int, int]) -> bytes:
    """Pack a BGR frame into the bytes ``scSendFrame`` expects.

    No channel swap and no vertical flip — see this module's docstring for why
    both would be wrong. Frames can arrive as non-contiguous views, so the
    buffer is materialized rather than assumed packed.
    """
    width, height = size
    if frame.ndim != 3 or frame.shape[2] != BYTES_PER_PIXEL:
        raise ValueError(f"expected a 3-channel BGR frame, got {frame.shape}")
    if frame.shape[:2] != (height, width):
        raise ValueError(
            f"frame is {frame.shape[1]}x{frame.shape[0]}, sink expects {width}x{height}"
        )
    return np.ascontiguousarray(frame, dtype=np.uint8).tobytes()


class SoftcamCamera(Protocol):
    """An opened softcam virtual camera the sink pushes frames into."""

    def send(self, data: bytes) -> None:
        """Send one whole frame."""
        ...

    def connected(self) -> bool:
        """Whether an application has the camera open."""
        ...

    def close(self) -> None:
        """Release the camera."""
        ...


#: Takes ``(width, height)`` and returns an opened :class:`SoftcamCamera`.
CameraFactory = Callable[[int, int], SoftcamCamera]


class SoftcamSink:
    """An :class:`~benchhud.output.sink.OutputSink` backed by softcam.

    Frames arrive from OpenCV as BGR and go out as BGR — this sink converts
    nothing. The frame size is fixed when the camera is created, so a mis-sized
    frame is rejected rather than allowed to desync the stream.
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        *,
        camera_factory: CameraFactory | None = None,
    ) -> None:
        validate_dimensions(width, height)
        self._size = (width, height)
        factory = camera_factory or open_softcam_camera
        self._camera: SoftcamCamera | None = factory(width, height)

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def connected(self) -> bool:
        """Whether anything is actually watching — ``scIsConnected``."""
        return self._camera is not None and self._camera.connected()

    def send(self, frame: np.ndarray) -> None:
        if self._camera is None:
            raise RuntimeError("sink is closed")
        self._camera.send(frame_to_buffer(frame, self._size))

    def close(self) -> None:
        if self._camera is None:
            return
        self._camera.close()
        self._camera = None


#: Told to the operator when Windows has no softcam registered. The DLL ships
#: with benchhud but camera apps only find it once it is registered, which
#: takes administrator approval one time. An install for all users does it; an
#: install for one user can't, so the fix is named here.
SOFTCAM_NOT_REGISTERED = (
    "the Windows virtual camera isn't registered yet — "
    "open Preferences, Output, and choose Register virtual camera "
    "(Windows asks for administrator approval once)"
)

#: Told to the operator when there is no softcam.dll to register or load: a run
#: from source, or a registration left pointing at a file that has gone.
SOFTCAM_DLL_MISSING = (
    "the Windows virtual camera's softcam.dll is missing — "
    "install benchhud from its installer, which includes it"
)

#: Shown when the DLL loaded but the camera wouldn't be created. softcam allows
#: exactly one sender system-wide (``scCreateCamera`` "fails if another instance
#: already exists"), which is the same single-writer constraint macOS has.
SOFTCAM_CAMERA_BUSY = (
    "the softcam virtual camera is already in use — "
    "another program is already sending to it; close it, then re-enable output"
)


#: Shown when the configured frame size isn't one softcam accepts. The size
#: comes from user-editable config, so this is a settings problem, not a driver
#: one, and it names the setting rather than the constraint.
SOFTCAM_BAD_SIZE = (
    "the configured output size doesn't work with the Windows virtual camera — "
    "set the output width and height to multiples of four (1280x720 by default)"
)


class SoftcamUnavailable(RuntimeError):
    """The softcam virtual camera can't be published to right now.

    ``reason`` is operator-facing — it names what to go fix, and it's what the
    shell puts in the status bar. Distinct situations get distinct reasons,
    because they need different fixes: the DLL isn't registered
    (:data:`SOFTCAM_NOT_REGISTERED`), there is no DLL
    (:data:`SOFTCAM_DLL_MISSING`), something else already holds the camera
    (:data:`SOFTCAM_CAMERA_BUSY`), or the configured size is illegal
    (:data:`SOFTCAM_BAD_SIZE`).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SoftcamNotRegistered(OSError):
    """Windows has no softcam registered, so no camera app could see the frames."""


def open_softcam_sink(
    width: int,
    height: int,
    *,
    camera_factory: CameraFactory | None = None,
) -> SoftcamSink:
    """Open a sink into a softcam virtual camera.

    Raises :class:`SoftcamUnavailable` — never a bare ``ctypes`` or size error —
    with a reason the operator can act on. Callers that must not raise catch it
    and fall back; the reason travels with the exception rather than being
    re-derived after the fact. The size comes from user-editable config and
    reaches this through ``start_output``, which doesn't guard, so an illegal
    one has to fall back rather than take the HUD down with it.
    """
    try:
        return SoftcamSink(width, height, camera_factory=camera_factory)
    except ValueError as exc:
        log.warning("softcam won't take the configured output size: %s", exc)
        raise SoftcamUnavailable(f"{SOFTCAM_BAD_SIZE} ({exc})") from exc
    except SoftcamNotRegistered as exc:
        log.warning("softcam isn't registered with Windows: %s", exc)
        raise SoftcamUnavailable(SOFTCAM_NOT_REGISTERED) from exc
    except OSError as exc:
        # ctypes raises OSError when the DLL itself can't be loaded.
        log.warning("could not load softcam's DLL: %s", exc)
        raise SoftcamUnavailable(f"{SOFTCAM_DLL_MISSING} ({exc})") from exc
    except RuntimeError as exc:
        log.warning("could not open the softcam virtual camera: %s", exc)
        raise SoftcamUnavailable(f"{SOFTCAM_CAMERA_BUSY} ({exc})") from exc


class _SoftcamHandle:
    """A live ``scCamera`` handle, released on close."""

    def __init__(self, library: object, handle: int) -> None:
        self._library = library
        self._handle: int | None = handle

    def send(self, data: bytes) -> None:
        if self._handle is None:
            raise RuntimeError("camera is closed")
        self._library.scSendFrame(self._handle, data)  # type: ignore[attr-defined]

    def connected(self) -> bool:
        if self._handle is None:
            return False
        return bool(self._library.scIsConnected(self._handle))  # type: ignore[attr-defined]

    def close(self) -> None:
        if self._handle is None:
            return
        self._library.scDeleteCamera(self._handle)  # type: ignore[attr-defined]
        self._handle = None


def open_softcam_camera(width: int, height: int) -> SoftcamCamera:
    """Load the registered softcam DLL and create the virtual camera.

    Raises :class:`SoftcamNotRegistered` if Windows has no softcam registered,
    ``OSError`` if the registered DLL can't be loaded, and ``RuntimeError`` if
    the camera couldn't be created — ``scCreateCamera`` returns a null pointer
    when another sender already holds it. Callers that must stay up should fall
    back; see :func:`benchhud.output.sink.open_output_sink`.
    """
    import ctypes  # Imported here so the module stays importable off Windows.

    dll = registered_dll_path()
    if dll is None:
        raise SoftcamNotRegistered(f"no InprocServer32 for CLSID {SOFTCAM_CLSID}")
    if not dll.is_file():
        raise FileNotFoundError(f"registered at {dll}, which is not there")

    # By full path: Python doesn't search the app's folders for a bare name.
    # __cdecl, per softcam.h — CDLL, not WinDLL. Exports are undecorated.
    library = ctypes.CDLL(str(dll))
    try:
        library.scCreateCamera.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_float]
        library.scCreateCamera.restype = ctypes.c_void_p
        library.scDeleteCamera.argtypes = [ctypes.c_void_p]
        library.scDeleteCamera.restype = None
        library.scSendFrame.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        library.scSendFrame.restype = None
        library.scIsConnected.argtypes = [ctypes.c_void_p]
        library.scIsConnected.restype = ctypes.c_bool
    except AttributeError as exc:
        # Another program's older softcam can be the registered one.
        raise OSError(f"{dll} is not a softcam this benchhud can use ({exc})") from exc

    # C++ default arguments don't survive into the export — pass all three.
    handle = library.scCreateCamera(width, height, REALTIME_FRAMERATE)
    if not handle:
        raise RuntimeError(
            f"scCreateCamera returned NULL for {width}x{height} — "
            "another sender already holds the camera"
        )
    return _SoftcamHandle(library, handle)
