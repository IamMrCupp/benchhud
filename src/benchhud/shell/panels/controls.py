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
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
from benchhud.shell.panel import PANEL_MARGINS, Panel
from benchhud.shell.qt_util import fit_combo_popup, grow_fields


class ControlsPanel(Panel):
    panel_id = "controls"
    panel_title = "Controls"

    changed = Signal()
    calibrate_requested = Signal()
    undo_requested = Signal()

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        settings = settings_from_config((config or Config()).overlay)
        transform = transform_from_config((config or Config()).registration)
        # The colour range (#244): Auto follows the board; Manual is fixed.
        self.range_combo = QComboBox()
        self.range_combo.addItems(["Auto", "Manual"])
        self.range_combo.setCurrentText("Auto" if settings.auto_range else "Manual")
        self.range_combo.setToolTip(
            "Auto: ambient to the hottest point, so warm parts use the whole palette"
        )
        self.tmin_spin = _spin(-40.0, 500.0, 1.0, settings.tmin, " °C")
        self.tmax_spin = _spin(-40.0, 500.0, 1.0, settings.tmax, " °C")
        self.scale_bar_check = QCheckBox("Show the colour scale")
        self.scale_bar_check.setChecked(settings.scale_bar)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([m.value for m in OverlayMode])
        self.mode_combo.setCurrentText(settings.mode.value)

        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(colormap_names())
        self.colormap_combo.setCurrentText(colormap_to_name(settings.colormap))
        fit_combo_popup(self.mode_combo)
        fit_combo_popup(self.colormap_combo)

        self.alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.alpha_slider.setRange(0, 100)
        self.alpha_slider.setValue(round(settings.alpha * 100))

        self.threshold_spin = _spin(0.0, 300.0, 1.0, settings.threshold, " °C")
        self._fit = transform.fit
        self._rotated_fit = transform.rotated_fit
        self.scale_spin = _spin(0.05, 30.0, 0.05, transform.scale)
        self.scale_spin.setToolTip("1.00 = the thermal fills the scope view; higher zooms it in")
        self.offset_x_spin = _spin(-2000.0, 2000.0, 1.0, transform.offset_x, " px")
        self.offset_y_spin = _spin(-2000.0, 2000.0, 1.0, transform.offset_y, " px")
        self.rotation_spin = _spin(-180.0, 180.0, 0.5, transform.rotation_deg, "°")

        form = QFormLayout(self)
        form.setContentsMargins(*PANEL_MARGINS)

        grow_fields(form)
        range_row = QHBoxLayout()
        range_row.addWidget(self.tmin_spin)
        range_row.addWidget(QLabel("to"))
        range_row.addWidget(self.tmax_spin)
        self._range_row = QWidget()
        range_row.setContentsMargins(0, 0, 0, 0)
        self._range_row.setLayout(range_row)

        form.addRow("Mode", self.mode_combo)
        form.addRow("Range", self.range_combo)
        form.addRow("", self._range_row)
        form.addRow("", self.scale_bar_check)
        form.addRow("Colormap", self.colormap_combo)
        form.addRow("Opacity", self.alpha_slider)
        form.addRow("Threshold", self.threshold_spin)
        self.calibrate_button = QPushButton("Calibrate…")
        self.calibrate_button.setToolTip("Line the thermal up with two touches of a hot iron tip")
        self.calibrate_button.clicked.connect(self.calibrate_requested)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setToolTip("Go back to the alignment before the last calibration")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.undo_requested)
        self.calibration_status = QLabel()
        self.calibration_status.setWordWrap(True)
        self.calibration_status.setVisible(False)
        calibrate_row = QHBoxLayout()
        calibrate_row.addWidget(self.calibrate_button, stretch=1)
        calibrate_row.addWidget(self.undo_button)

        form.addRow(calibrate_row)
        form.addRow(self.calibration_status)
        form.addRow("Scale", self.scale_spin)
        form.addRow("Offset X", self.offset_x_spin)
        form.addRow("Offset Y", self.offset_y_spin)
        # Quarter turns (#259): a portrait thermal on a landscape scope.
        self.turn_left_button = QPushButton("⟲")
        self.turn_left_button.setToolTip("Turn the thermal 90° counter-clockwise")
        self.turn_left_button.clicked.connect(lambda: self.turn(1))
        self.turn_right_button = QPushButton("⟳")
        self.turn_right_button.setToolTip("Turn the thermal 90° clockwise")
        self.turn_right_button.clicked.connect(lambda: self.turn(-1))
        # On their own line: beside the spin box they made the panel wider than a
        # narrow dock.
        turn_row = QHBoxLayout()
        turn_row.addWidget(self.turn_left_button)
        turn_row.addWidget(self.turn_right_button)
        turn_row.addStretch(1)
        form.addRow("Rotation", self.rotation_spin)
        form.addRow("", turn_row)

        self.mode_combo.currentTextChanged.connect(self._on_change)
        self.range_combo.currentTextChanged.connect(self._on_change)
        self.tmin_spin.valueChanged.connect(self._on_change)
        self.tmax_spin.valueChanged.connect(self._on_change)
        self.scale_bar_check.toggled.connect(self._on_change)
        self._range_row.setVisible(not settings.auto_range)
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
            tmin=self.tmin_spin.value(),
            tmax=self.tmax_spin.value(),
            auto_range=self.range_combo.currentText() == "Auto",
            scale_bar=self.scale_bar_check.isChecked(),
            colormap=colormap_from_name(self.colormap_combo.currentText()),
        )

    def current_transform(self) -> RegistrationTransform:
        return RegistrationTransform(
            scale=self.scale_spin.value(),
            offset_x=self.offset_x_spin.value(),
            offset_y=self.offset_y_spin.value(),
            rotation_deg=self.rotation_spin.value(),
            fit=self._fit,
            rotated_fit=self._rotated_fit,
        )

    def show_calibration(self, text: str, *, error: bool = False) -> None:
        """Calibration's step-by-step prompt (empty hides it)."""
        self.calibration_status.setText(text)
        self.calibration_status.setVisible(bool(text))
        self.calibration_status.setStyleSheet("color: #ff7a7a;" if error else "")

    def turn(self, quarter_turns: int) -> None:
        """Turn the thermal by 90° steps; the fit follows, so it still fills the view."""
        self.set_transform(self.current_transform().turned(quarter_turns))

    def set_transform(self, transform: RegistrationTransform) -> None:
        """Show (and save) a transform found elsewhere: migration, calibration."""
        spins = (self.scale_spin, self.offset_x_spin, self.offset_y_spin, self.rotation_spin)
        for spin in spins:
            spin.blockSignals(True)
        self._fit = transform.fit
        self._rotated_fit = transform.rotated_fit
        self.scale_spin.setValue(transform.scale)
        self.offset_x_spin.setValue(transform.offset_x)
        self.offset_y_spin.setValue(transform.offset_y)
        self.rotation_spin.setValue(transform.rotation_deg)
        for spin in spins:
            spin.blockSignals(False)
        self._on_change()

    def _on_change(self, *_: object) -> None:
        self._range_row.setVisible(self.range_combo.currentText() == "Manual")
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
