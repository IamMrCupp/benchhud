"""Controls panel — live overlay + registration adjustment.

Exposes the overlay mode / colormap / opacity / threshold and the registration
nudges (scale, offset, rotation). Initialized from config; on any change it
writes the values back into the config object (persisted when the window closes)
and emits :attr:`changed` so a live compositor can re-render. The compositor
wiring itself is bench work — this panel is the control surface, testable now.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QWheelEvent
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
from benchhud.overlay.reference import CompareMode, ReferenceSettings
from benchhud.overlay.transform import RegistrationTransform
from benchhud.shell.panel import PANEL_MARGINS, Panel
from benchhud.shell.qt_util import fit_combo_popup, grow_fields


class ControlsPanel(Panel):
    panel_id = "controls"
    panel_title = "Controls"

    changed = Signal()
    calibrate_requested = Signal()
    #: Known-good board comparison (#256).
    reference_capture_requested = Signal()
    reference_load_requested = Signal()
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
        self._quarter_fit = transform.quarter_fit
        # Fine enough to line the thermal up (#318): a 0.005 scale step is ~5 px
        # at the edge of a 1080p frame, a 0.1° turn ~2 px.
        self.scale_spin = _spin(0.05, 30.0, 0.005, transform.scale, decimals=3)
        self.scale_spin.setToolTip("1.000 = the thermal fills the scope view; higher zooms it in")
        self.offset_x_spin = _spin(-2000.0, 2000.0, 1.0, transform.offset_x, " px")
        self.offset_y_spin = _spin(-2000.0, 2000.0, 1.0, transform.offset_y, " px")
        self.rotation_spin = _spin(-180.0, 180.0, 0.1, transform.rotation_deg, "°")
        # A calibrated transform at full precision, until a control is changed:
        # the spin boxes round what they show (#318).
        self._precise: RegistrationTransform | None = transform

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

        # Compare with a known-good board (#256). Starts Off every launch: it's
        # a diagnostic aid, not a look.
        self.reference_mode = QComboBox()
        for mode in CompareMode:
            self.reference_mode.addItem(mode.value.capitalize(), mode.value)
        self.reference_mode.setToolTip(
            "Overlay fades the good board over the live view; Blink flips between them"
        )
        self.reference_opacity = QSlider(Qt.Orientation.Horizontal)
        self.reference_opacity.setRange(0, 100)
        self.reference_opacity.setValue(50)
        self.reference_capture_button = QPushButton("Use this frame")
        self.reference_capture_button.setToolTip(
            "Save the current scope frame as this board's known-good reference"
        )
        self.reference_capture_button.clicked.connect(self.reference_capture_requested)
        self.reference_load_button = QPushButton("Load photo…")
        self.reference_load_button.clicked.connect(self.reference_load_requested)
        self.reference_status = QLabel("No reference for this board.")
        self.reference_status.setWordWrap(True)
        reference_buttons = QHBoxLayout()
        reference_buttons.addWidget(self.reference_capture_button, stretch=1)
        reference_buttons.addWidget(self.reference_load_button, stretch=1)
        form.addRow(QLabel("Known-good board"))
        form.addRow("Compare", self.reference_mode)
        form.addRow("Opacity", self.reference_opacity)
        form.addRow(reference_buttons)
        form.addRow(self.reference_status)
        self.reference_mode.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self.reference_opacity.valueChanged.connect(lambda _v: self.changed.emit())

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
        shown = RegistrationTransform(
            scale=self.scale_spin.value(),
            offset_x=self.offset_x_spin.value(),
            offset_y=self.offset_y_spin.value(),
            rotation_deg=self.rotation_spin.value(),
            fit=self._fit,
            rotated_fit=self._rotated_fit,
            quarter_fit=self._quarter_fit,
        )
        if self._precise is not None and self._shows(self._precise):
            return self._precise
        self._precise = None  # a control was changed: what's shown is the value
        return shown

    def _shows(self, transform: RegistrationTransform) -> bool:
        """Whether the spin boxes show ``transform``, as rounded as they display it."""
        pairs = (
            (self.scale_spin, transform.scale),
            (self.offset_x_spin, transform.offset_x),
            (self.offset_y_spin, transform.offset_y),
            (self.rotation_spin, transform.rotation_deg),
        )
        same_fit = (transform.fit, transform.rotated_fit, transform.quarter_fit) == (
            self._fit,
            self._rotated_fit,
            self._quarter_fit,
        )
        return same_fit and all(
            round(value, spin.decimals()) == spin.value() for spin, value in pairs
        )

    def reference_settings(self) -> ReferenceSettings:
        return ReferenceSettings(
            mode=CompareMode(self.reference_mode.currentData() or "off"),
            opacity=self.reference_opacity.value() / 100.0,
        )

    def show_reference(self, text: str, *, error: bool = False) -> None:
        self.reference_status.setText(text)
        self.reference_status.setStyleSheet("color: #ff7a7a;" if error else "")

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
        self._quarter_fit = transform.quarter_fit
        self._precise = transform
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


class _SpinBox(QDoubleSpinBox):
    """A spin box the wheel only turns once it's been clicked into (#318).

    The panel sits in a scroll area, and a plain spin box took the wheel
    whenever the pointer passed over it: scrolling the dock nudged the scale.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()  # let the scroll area have it


def _spin(
    lo: float, hi: float, step: float, value: float, suffix: str = "", decimals: int = 2
) -> QDoubleSpinBox:
    spin = _SpinBox()
    spin.setDecimals(decimals)
    spin.setRange(lo, hi)
    spin.setSingleStep(step)
    spin.setValue(value)
    if suffix:
        spin.setSuffix(suffix)
    return spin
