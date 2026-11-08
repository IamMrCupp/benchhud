"""Cameras panel: aim the stream cameras and jump between saved shots (#330).

OBS keeps every camera's picture. This panel only moves them: pan, tilt and
zoom through V4L2 controls (see :mod:`benchhud.devices.camera_controls`), with
sliders built from whatever ranges each camera reports. A camera with no such
controls just gets none.

Shots (Face, Desk, Reflow) are saved here, in benchhud's config, not in the
camera: the EMEET PIXY's own preset slots don't hold zoom and can't be
reliably recalled.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from benchhud.config import CameraConfig
from benchhud.devices.camera_controls import (
    ARCSEC_PER_DEGREE,
    AXES,
    CameraControls,
    ControlRange,
)
from benchhud.devices.discovery import DeviceScanner
from benchhud.shell.panel import PANEL_MARGINS, Panel

#: Builds the controls for a device path (a fake in tests).
ControlsFactory = Callable[[str], CameraControls]
#: (title, label, default) → text, or None when cancelled.
AskText = Callable[[str, str, str], str | None]
#: (title, label, items) → the chosen item, or None when cancelled.
AskItem = Callable[[str, str, list[str]], str | None]
Confirm = Callable[[str, str], bool]

_LABELS = {"pan": "Pan", "tilt": "Tilt", "zoom": "Zoom"}


def describe(axis: str, value: int, rng: ControlRange) -> str:
    """A control value the way a person reads it: degrees, or a zoom factor."""
    if axis in ("pan", "tilt"):
        return f"{value / ARCSEC_PER_DEGREE:+.1f}°"
    if rng.minimum > 0:
        return f"{value / rng.minimum:.2f}x"
    return str(value)


class CamerasPanel(Panel):
    panel_id = "cameras"
    panel_title = "Cameras"

    def __init__(
        self,
        cameras: list[CameraConfig],
        *,
        scanner: DeviceScanner | None = None,
        controls_factory: ControlsFactory = CameraControls,
        ask_text: AskText | None = None,
        ask_item: AskItem | None = None,
        confirm: Confirm | None = None,
        platform: str = sys.platform,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._cameras = cameras  # the live config list: edits here are saved on quit
        self._scanner = scanner
        self._factory = controls_factory
        self._ask_text = ask_text or self._ask_text_dialog
        self._ask_item = ask_item or self._ask_item_dialog
        self._confirm = confirm or self._confirm_dialog
        self._supported = platform.startswith("linux")
        self._controls: CameraControls | None = None
        self._axes: dict[str, ControlRange] = {}

        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumContentsLength(8)
        self.camera_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.camera_combo.currentIndexChanged.connect(lambda _i: self.reload())
        self.add_button = QPushButton("Add…")
        self.add_button.setToolTip("Add a camera to aim from here")
        self.add_button.clicked.connect(self.add_camera)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_camera)
        self.reload_button = QPushButton("Read")
        self.reload_button.setToolTip("Read the camera's controls and where it's pointing now")
        self.reload_button.clicked.connect(self.reload)

        self.status = QLabel()
        self.status.setWordWrap(True)

        self.sliders: dict[str, QSlider] = {}
        self.values: dict[str, QLabel] = {}
        self._names: dict[str, QLabel] = {}
        grid = QGridLayout()
        for row, axis in enumerate(AXES):
            name = QLabel(_LABELS[axis])
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setMinimumWidth(60)
            value = QLabel()
            value.setMinimumWidth(48)
            # Move on release while dragging (a motor can't follow every tick),
            # and at once for a click or a key.
            slider.sliderReleased.connect(lambda a=axis: self._slider_moved(a))
            slider.valueChanged.connect(lambda _v, a=axis: self._slider_changed(a))
            grid.addWidget(name, row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(value, row, 2)
            self._names[axis] = name
            self.sliders[axis] = slider
            self.values[axis] = value

        self.shot_combo = QComboBox()
        self.shot_combo.setMinimumContentsLength(8)
        self.shot_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.shot_combo.setPlaceholderText("No shots saved")
        self.go_button = QPushButton("Go")
        self.go_button.setToolTip("Move the camera to this shot")
        self.go_button.clicked.connect(lambda: self.go_to_shot(self.shot_combo.currentText()))
        self.save_button = QPushButton("Save shot…")
        self.save_button.setToolTip("Save where the camera points now as a named shot")
        self.save_button.clicked.connect(self.save_shot)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self.delete_shot)

        cam_row = QHBoxLayout()
        cam_row.addWidget(self.camera_combo, 1)
        cam_row.addWidget(self.reload_button)
        cam_buttons = QHBoxLayout()
        cam_buttons.addWidget(self.add_button)
        cam_buttons.addWidget(self.remove_button)
        cam_buttons.addStretch(1)
        shot_row = QHBoxLayout()
        shot_row.addWidget(self.shot_combo, 1)
        shot_row.addWidget(self.go_button)
        shot_buttons = QHBoxLayout()
        shot_buttons.addWidget(self.save_button)
        shot_buttons.addWidget(self.delete_button)
        shot_buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addLayout(cam_row)
        layout.addLayout(cam_buttons)
        layout.addWidget(self.status)
        layout.addLayout(grid)
        layout.addWidget(QLabel("Shots"))
        layout.addLayout(shot_row)
        layout.addLayout(shot_buttons)
        layout.addStretch(1)

        self._fill_cameras()

    # --- state -----------------------------------------------------------

    def camera(self) -> CameraConfig | None:
        index = self.camera_combo.currentIndex()
        return self._cameras[index] if 0 <= index < len(self._cameras) else None

    def _fill_cameras(self, select: int | None = None) -> None:
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        for cam in self._cameras:
            self.camera_combo.addItem(cam.name)
        if self._cameras:
            self.camera_combo.setCurrentIndex(select if select is not None else 0)
        self.camera_combo.blockSignals(False)
        self.reload()

    def _fill_shots(self, select: str | None = None) -> None:
        cam = self.camera()
        self.shot_combo.clear()
        if cam is not None:
            self.shot_combo.addItems(list(cam.shots))
            if select in cam.shots:
                self.shot_combo.setCurrentText(select)
        self._update_enabled()

    def _update_enabled(self) -> None:
        cam = self.camera()
        live = self._supported and self._controls is not None
        self.add_button.setEnabled(self._supported and self._scanner is not None)
        self.remove_button.setEnabled(cam is not None)
        self.reload_button.setEnabled(self._supported and cam is not None)
        has_shot = cam is not None and bool(cam.shots)
        self.go_button.setEnabled(live and bool(self._axes) and has_shot)
        self.delete_button.setEnabled(has_shot)
        self.save_button.setEnabled(live and bool(self._axes))

    def _show_axes(self) -> None:
        for axis, slider in self.sliders.items():
            rng = self._axes.get(axis)
            shown = rng is not None
            for widget in (self._names[axis], slider, self.values[axis]):
                widget.setVisible(shown)
            if rng is None:
                continue
            slider.blockSignals(True)
            slider.setRange(rng.minimum, rng.maximum)
            slider.setSingleStep(rng.step)
            slider.setPageStep(max(rng.step, (rng.maximum - rng.minimum) // 10))
            slider.blockSignals(False)

    def _set_slider(self, axis: str, value: int) -> None:
        slider = self.sliders[axis]
        slider.blockSignals(True)
        slider.setValue(value)
        slider.blockSignals(False)
        self.values[axis].setText(describe(axis, value, self._axes[axis]))

    def reload(self) -> None:
        """Ask the selected camera what it offers and where it points now."""
        self._controls = None
        self._axes = {}
        cam = self.camera()
        if not self._supported:
            self.status.setText("Aiming cameras needs Linux (V4L2 controls).")
        elif cam is None:
            self.status.setText("No cameras yet. Add… picks one to aim from here.")
        else:
            controls = self._factory(cam.device)
            try:
                axes = controls.axes()
                positions = controls.positions(axes)
            except OSError as exc:
                self.status.setText(f"Can't reach {cam.name}: {exc.strerror or exc}")
            else:
                self._controls = controls
                self._axes = axes
                if axes:
                    self.status.setText("")
                else:
                    self.status.setText(f"{cam.name} has no pan, tilt or zoom to set.")
                self._show_axes()
                for axis, value in positions.items():
                    self._set_slider(axis, value)
        if not self._axes:
            self._show_axes()
        self._fill_shots()

    # --- moving ------------------------------------------------------------

    def _slider_changed(self, axis: str) -> None:
        rng = self._axes.get(axis)
        if rng is None:
            return
        slider = self.sliders[axis]
        self.values[axis].setText(describe(axis, slider.value(), rng))
        if not slider.isSliderDown():
            self._slider_moved(axis)

    def _slider_moved(self, axis: str) -> None:
        self._move({axis: self.sliders[axis].value()})

    def _move(self, values: dict[str, int]) -> bool:
        if self._controls is None:
            return False
        try:
            written = self._controls.move_to(values, self._axes)
        except OSError as exc:
            cam = self.camera()
            name = cam.name if cam else "the camera"
            self.status.setText(f"Couldn't move {name}: {exc.strerror or exc}")
            return False
        for axis, value in written.items():
            self._set_slider(axis, value)
        self.status.setText("")
        return True

    def go_to_shot(self, name: str) -> bool:
        """Move the selected camera to a saved shot. False if it didn't move."""
        cam = self.camera()
        if cam is None or name not in cam.shots:
            return False
        shot = cam.shots[name]
        if not self._move(shot):
            return False
        missing = sorted(set(shot) - set(self._axes))
        if missing:
            self.status.setText(f"{cam.name} has no {', '.join(missing)}; moved the rest.")
        return True

    # --- shots -------------------------------------------------------------

    def save_shot(self) -> None:
        cam = self.camera()
        if cam is None or not self._axes:
            return
        name = self._ask_text("Save shot", "Name this shot (e.g. Face, Desk, Reflow):", "")
        name = (name or "").strip()
        if not name:
            return
        if name in cam.shots and not self._confirm(
            "Replace shot", f"Replace the saved shot “{name}”?"
        ):
            return
        cam.shots[name] = {axis: self.sliders[axis].value() for axis in self._axes}
        self._fill_shots(select=name)

    def delete_shot(self) -> None:
        cam = self.camera()
        name = self.shot_combo.currentText()
        if cam is None or name not in cam.shots:
            return
        if self._confirm("Delete shot", f"Delete the shot “{name}”?"):
            del cam.shots[name]
            self._fill_shots()

    # --- cameras -----------------------------------------------------------

    def add_camera(self) -> None:
        if self._scanner is None:
            return
        found = self._scanner.cameras()
        known = {cam.device for cam in self._cameras}
        choices = {f"{c.label} ({c.identifier})": c for c in found if c.identifier not in known}
        if not choices:
            self.status.setText("No other cameras found.")
            return
        picked = self._ask_item("Add camera", "Camera to aim:", list(choices))
        if picked is None or picked not in choices:
            return
        device = choices[picked]
        name = self._ask_text("Add camera", "Name it:", device.label)
        name = (name or "").strip() or device.label
        self._cameras.append(CameraConfig(name=name, device=device.identifier))
        self._fill_cameras(select=len(self._cameras) - 1)

    def remove_camera(self) -> None:
        cam = self.camera()
        if cam is None:
            return
        shots = f" and its {len(cam.shots)} saved shot(s)" if cam.shots else ""
        if self._confirm("Remove camera", f"Remove {cam.name}{shots} from this panel?"):
            self._cameras.remove(cam)
            self._fill_cameras()

    # --- dialogs -----------------------------------------------------------

    def _ask_text_dialog(self, title: str, label: str, default: str) -> str | None:
        text, ok = QInputDialog.getText(self, title, label, text=default)
        return text if ok else None

    def _ask_item_dialog(self, title: str, label: str, items: list[str]) -> str | None:
        item, ok = QInputDialog.getItem(self, title, label, items, 0, False)
        return item if ok else None

    def _confirm_dialog(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
