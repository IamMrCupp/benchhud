"""Controls panel — live overlay + registration adjustment.

Exposes the overlay mode / colormap / opacity / threshold and the registration
nudges (scale, offset, rotation). Initialized from config; on any change it
writes the values back into the config object (persisted when the window closes)
and emits :attr:`changed` so a live compositor can re-render. The compositor
wiring itself is bench work — this panel is the control surface, testable now.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QSlider,
    QWidget,
)

from benchhud.config import Config
from benchhud.overlay.composite import OverlayMode, OverlaySettings
from benchhud.overlay.config_adapter import (
    colormap_from_name,
    colormap_names,
    colormap_to_name,
    config_from_settings,
    config_from_transform,
    settings_from_config,
    transform_from_config,
)
from benchhud.overlay.transform import RegistrationTransform
from benchhud.shell.panel import Panel


class ControlsPanel(Panel):
    panel_id = "controls"
    panel_title = "Controls"

    changed = Signal()

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        settings = settings_from_config((config or Config()).overlay)
        transform = transform_from_config((config or Config()).registration)
        # tmin/tmax aren't exposed as controls yet — preserve them.
        self._tmin = settings.tmin
        self._tmax = settings.tmax

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([m.value for m in OverlayMode])
        self.mode_combo.setCurrentText(settings.mode.value)

        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(colormap_names())
        self.colormap_combo.setCurrentText(colormap_to_name(settings.colormap))

        self.alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.alpha_slider.setRange(0, 100)
        self.alpha_slider.setValue(round(settings.alpha * 100))

        self.threshold_spin = _spin(0.0, 300.0, 1.0, settings.threshold, " °C")
        self.scale_spin = _spin(0.05, 10.0, 0.05, transform.scale)
        self.offset_x_spin = _spin(-2000.0, 2000.0, 1.0, transform.offset_x, " px")
        self.offset_y_spin = _spin(-2000.0, 2000.0, 1.0, transform.offset_y, " px")
        self.rotation_spin = _spin(-180.0, 180.0, 0.5, transform.rotation_deg, "°")

        form = QFormLayout(self)
        form.addRow("Mode", self.mode_combo)
        form.addRow("Colormap", self.colormap_combo)
        form.addRow("Opacity", self.alpha_slider)
        form.addRow("Threshold", self.threshold_spin)
        form.addRow("Scale", self.scale_spin)
        form.addRow("Offset X", self.offset_x_spin)
        form.addRow("Offset Y", self.offset_y_spin)
        form.addRow("Rotation", self.rotation_spin)

        self.mode_combo.currentTextChanged.connect(self._on_change)
        self.colormap_combo.currentTextChanged.connect(self._on_change)
        self.alpha_slider.valueChanged.connect(self._on_change)
        for spin in (
            self.threshold_spin,
            self.scale_spin,
            self.offset_x_spin,
            self.offset_y_spin,
            self.rotation_spin,
        ):
            spin.valueChanged.connect(self._on_change)

    def current_settings(self) -> OverlaySettings:
        return OverlaySettings(
            mode=OverlayMode(self.mode_combo.currentText()),
            alpha=self.alpha_slider.value() / 100.0,
            threshold=self.threshold_spin.value(),
            tmin=self._tmin,
            tmax=self._tmax,
            colormap=colormap_from_name(self.colormap_combo.currentText()),
        )

    def current_transform(self) -> RegistrationTransform:
        return RegistrationTransform(
            scale=self.scale_spin.value(),
            offset_x=self.offset_x_spin.value(),
            offset_y=self.offset_y_spin.value(),
            rotation_deg=self.rotation_spin.value(),
        )

    def _on_change(self, *_: object) -> None:
        if self._config is not None:
            self._config.overlay = config_from_settings(self.current_settings())
            self._config.registration = config_from_transform(self.current_transform())
        self.changed.emit()


def _spin(lo: float, hi: float, step: float, value: float, suffix: str = "") -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(lo, hi)
    spin.setSingleStep(step)
    spin.setValue(value)
    if suffix:
        spin.setSuffix(suffix)
    return spin
