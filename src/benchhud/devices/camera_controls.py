"""Pan, tilt and zoom on a USB camera through V4L2 controls (#330).

A camera's controls are separate from its picture: Linux lets another program
open the same ``/dev/video`` node and change them while OBS streams it, because
only buffer and format calls belong to whoever owns the stream. So benchhud
never touches the picture here. It asks what the camera offers
(``VIDIOC_QUERYCTRL``), reads the current value and writes a new one.

Only absolute positions are used. A PTZ camera such as the EMEET PIXY moves
motors, and a "keep moving" command that's never stopped runs the gimbal to its
end stop; an absolute position always ends where it was asked to.
"""

from __future__ import annotations

import errno
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass

#: The camera-class control ids (linux/v4l2-controls.h).
_CAMERA_CLASS_BASE = 0x009A0900
PAN_ABSOLUTE = _CAMERA_CLASS_BASE + 8
TILT_ABSOLUTE = _CAMERA_CLASS_BASE + 9
ZOOM_ABSOLUTE = _CAMERA_CLASS_BASE + 13

#: The axes benchhud drives, in the order a shot applies them: zoom first, so a
#: pan lands on the framing it was saved with.
AXES: dict[str, int] = {"zoom": ZOOM_ABSOLUTE, "pan": PAN_ABSOLUTE, "tilt": TILT_ABSOLUTE}

#: Pan and tilt are in arc-seconds (the V4L2 spec); 3600 to a degree.
ARCSEC_PER_DEGREE = 3600

# ioctl numbers: _IOWR('V', nr, struct) from linux/videodev2.h.
_VIDIOC_QUERYCTRL = 0xC0445624  # nr 36, struct v4l2_queryctrl (68 bytes)
_VIDIOC_G_CTRL = 0xC008561B  # nr 27, struct v4l2_control (8 bytes)
_VIDIOC_S_CTRL = 0xC008561C  # nr 28

_QUERYCTRL = struct.Struct("<II32siiiiI2I")  # id type name min max step default flags reserved
_CONTROL = struct.Struct("<Ii")  # id value

_FLAG_DISABLED = 0x0001
_FLAG_READ_ONLY = 0x0004

Ioctl = Callable[[int, int, bytes], bytes]


@dataclass(frozen=True)
class ControlRange:
    """What a camera reports for one control."""

    minimum: int
    maximum: int
    step: int
    default: int

    def clamp(self, value: int) -> int:
        """``value`` inside the range, on a step from the minimum."""
        value = max(self.minimum, min(self.maximum, int(value)))
        if self.step > 1:
            value = self.minimum + round((value - self.minimum) / self.step) * self.step
            value = min(value, self.maximum)
        return value


class CameraControls:
    """The pan/tilt/zoom controls of one ``/dev/video`` node.

    The node is opened for each call and closed again, so benchhud holds
    nothing open on a camera that OBS owns, and a camera that drops off USB and
    comes back under the same by-id link just works on the next call.
    """

    def __init__(
        self,
        device: str,
        *,
        ioctl: Ioctl | None = None,
        opener: Callable[[str, int], int] = os.open,
        closer: Callable[[int], None] = os.close,
    ) -> None:
        self.device = device
        if ioctl is None:
            import fcntl

            ioctl = fcntl.ioctl
        self._ioctl = ioctl
        self._open = opener
        self._close = closer

    def _call(self, request: int, payload: bytes) -> bytes:
        fd = self._open(self.device, os.O_RDWR | os.O_NONBLOCK)
        try:
            return self._ioctl(fd, request, payload)
        finally:
            self._close(fd)

    def query(self, control_id: int) -> ControlRange | None:
        """The control's range, or None when the camera doesn't offer it.

        Raises OSError when the device can't be opened at all.
        """
        payload = _QUERYCTRL.pack(control_id, 0, b"", 0, 0, 0, 0, 0, 0, 0)
        try:
            raw = self._call(_VIDIOC_QUERYCTRL, payload)
        except OSError as exc:
            if exc.errno == errno.EINVAL:  # the camera doesn't have this control
                return None
            raise
        _id, _type, _name, lo, hi, step, default, flags, _r0, _r1 = _QUERYCTRL.unpack(raw)
        # An inactive control (say pan while the camera tracks) is still offered:
        # it comes back once the mode that blocks it is off.
        if flags & (_FLAG_DISABLED | _FLAG_READ_ONLY) or hi <= lo:
            return None
        return ControlRange(lo, hi, max(1, step), default)

    def get(self, control_id: int) -> int:
        raw = self._call(_VIDIOC_G_CTRL, _CONTROL.pack(control_id, 0))
        return _CONTROL.unpack(raw)[1]

    def set(self, control_id: int, value: int) -> None:
        self._call(_VIDIOC_S_CTRL, _CONTROL.pack(control_id, int(value)))

    def axes(self) -> dict[str, ControlRange]:
        """The pan/tilt/zoom axes this camera offers, by name."""
        found: dict[str, ControlRange] = {}
        for name, control_id in AXES.items():
            rng = self.query(control_id)
            if rng is not None:
                found[name] = rng
        return found

    def positions(self, axes: dict[str, ControlRange]) -> dict[str, int]:
        """The current value of each of ``axes``."""
        return {name: self.get(AXES[name]) for name in axes}

    def move_to(self, values: dict[str, int], axes: dict[str, ControlRange]) -> dict[str, int]:
        """Set each axis in ``values`` the camera offers, clamped to its range.

        Applies them in :data:`AXES` order and returns what was written.
        """
        written: dict[str, int] = {}
        for name, control_id in AXES.items():
            if name in values and name in axes:
                value = axes[name].clamp(values[name])
                self.set(control_id, value)
                written[name] = value
        return written
