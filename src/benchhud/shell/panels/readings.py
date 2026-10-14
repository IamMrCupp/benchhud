"""Readings panel: test-point readings, compared with a known-good board (#235).

Type a test point and press Enter: the live DMM reading (or one typed by hand)
is saved on the selected job, and the table compares every point with the
known-good readings for the job's board number. Points the good board has and
this job doesn't yet are listed too, so the table doubles as a checklist.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from benchhud.instruments.owon_spm8104 import DmmReading
from benchhud.jobs.readings import MODES, Row, Verdict, compare, format_value, mode, mode_from_dmm
from benchhud.jobs.store import JobStore
from benchhud.shell.panel import PANEL_MARGINS, Panel

_VERDICT_COLOURS = {
    Verdict.OK: "#5ec27a",
    Verdict.LOW: "#ff7a7a",
    Verdict.HIGH: "#ff7a7a",
    Verdict.OPEN: "#ff7a7a",
    Verdict.TODO: "#8a93a3",
}


class ReadingsPanel(Panel):
    panel_id = "readings"
    panel_title = "Readings"

    #: A reading was recorded or changed: the job's notes/report may want a refresh.
    readings_changed = Signal()

    def __init__(
        self,
        store: JobStore,
        selected_job: Callable[[], int | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._selected_job = selected_job
        self._meter: DmmReading | None = None
        self.rows: list[Row] = []

        self.board_label = QLabel()
        self.board_label.setObjectName("ReadingsBoard")
        self.board_label.setWordWrap(True)

        self.point_edit = QLineEdit()
        self.point_edit.setPlaceholderText("Test point, e.g. PP3V3_S5")
        self.point_edit.setMinimumWidth(60)
        self.point_edit.returnPressed.connect(self.record)
        self._points = QStringListModel(self)
        completer = QCompleter(self._points, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.point_edit.setCompleter(completer)
        self.record_button = QPushButton("Record")
        self.record_button.clicked.connect(self.record)

        self.from_meter = QCheckBox("From the meter")
        self.from_meter.setChecked(True)
        self.from_meter.setToolTip("Record the live DMM reading. Untick to type a value.")
        self.from_meter.toggled.connect(self._show_meter)
        self.value_edit = QLineEdit()
        self.value_edit.setMinimumWidth(60)
        self.value_edit.setPlaceholderText("value or OL")
        self.mode_combo = QComboBox()
        for m in MODES:
            self.mode_combo.addItem(f"{m.label} ({m.unit})", m.key)
        self.reference_check = QCheckBox("This is a known-good board")
        self.reference_check.setToolTip(
            "Readings recorded while ticked become the reference for this board number"
        )

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Point", "This", "Good", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # The point name is what you scan for, so it's never the one cut short.
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellClicked.connect(self._pick_row)

        self.mark_good_button = QPushButton("Mark all known-good")
        self.mark_good_button.setToolTip(
            "Use this job's readings as the reference: once the board is proven fixed"
        )
        self.mark_good_button.clicked.connect(self.mark_known_good)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setToolTip("Delete this job's reading at the selected point")
        self.delete_button.clicked.connect(self.delete_selected)

        self.status = QLabel()
        self.status.setWordWrap(True)

        entry = QHBoxLayout()
        entry.addWidget(self.point_edit, stretch=1)
        entry.addWidget(self.record_button)
        value = QGridLayout()
        value.addWidget(self.from_meter, 0, 0, 1, 2)
        value.addWidget(self.value_edit, 1, 0)
        value.addWidget(self.mode_combo, 1, 1)
        value.setColumnStretch(0, 1)
        actions = QHBoxLayout()
        actions.addWidget(self.mark_good_button)
        actions.addStretch(1)
        actions.addWidget(self.delete_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addWidget(self.board_label)
        layout.addLayout(entry)
        layout.addLayout(value)
        layout.addWidget(self.reference_check)
        layout.addWidget(self.status)
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(actions)

        self._show_meter()
        self.refresh()

    # --- the meter ----------------------------------------------------------------

    def update_meter(self, dmm: DmmReading | None) -> None:
        """The latest DMM reading (None when there's no meter or it failed)."""
        self._meter = dmm
        if self.from_meter.isChecked():
            self._show_meter()

    def _show_meter(self) -> None:
        live = self.from_meter.isChecked()
        self.value_edit.setReadOnly(live)
        self.mode_combo.setEnabled(not live)
        if not live:
            return
        dmm = self._meter
        if dmm is None:
            self.value_edit.setText("")
            self.value_edit.setPlaceholderText("no meter reading")
            return
        key = mode_from_dmm(dmm.mode)
        found = mode(key) if key else None
        if found is not None:
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(found.key))
        self.value_edit.setText(format_value(dmm.value, dmm.status, found.unit if found else ""))

    def _current_value(self) -> tuple[str, float | None, str | None, str] | None:
        """(mode, value, status, unit) to record, or None with the reason shown."""
        if self.from_meter.isChecked():
            dmm = self._meter
            if dmm is None:
                self._say("No meter reading. Untick From the meter to type one.", error=True)
                return None
            key = mode_from_dmm(dmm.mode) or dmm.mode.lower()
            found = mode(key)
            return key, dmm.value, dmm.status, found.unit if found else ""
        key = self.mode_combo.currentData()
        text = self.value_edit.text().strip().replace(",", ".")
        found = mode(key)
        unit = found.unit if found else ""
        if text.upper() in ("OL", "OPEN"):
            return key, None, "OL", unit
        number = text.removesuffix(unit).strip() if unit else text
        try:
            return key, float(number), None, unit
        except ValueError:
            self._say("Type a number, or OL for open.", error=True)
            return None

    # --- recording ------------------------------------------------------------------

    def record(self) -> None:
        job_id = self._selected_job()
        if job_id is None:
            self._say("Select a job first.", error=True)
            return
        point = self.point_edit.text().strip()
        if not point:
            self._say("Type the test point first.", error=True)
            return
        value = self._current_value()
        if value is None:
            return
        key, number, status, unit = value
        reading = self._store.add_reading(
            job_id,
            point,
            key,
            number,
            status=status,
            unit=unit,
            reference=self.reference_check.isChecked(),
        )
        self.point_edit.clear()
        self.refresh()
        row = next(
            (r for r in self.rows if r.point == reading.point and r.mode == reading.mode), None
        )
        shown = format_value(reading.value, reading.status, unit)
        if row is None or row.verdict is Verdict.NEW:
            self._say(f"{reading.point}: {shown}.")
        else:
            self._say(f"{reading.point}: {shown}, {row.verdict.value}.", error=row.flagged)
        self.readings_changed.emit()

    def mark_known_good(self) -> None:
        job_id = self._selected_job()
        if job_id is None:
            return
        self._store.set_readings_reference(job_id, True)
        self._say("This job's readings are now the known-good reference for this board.")
        self.refresh()
        self.readings_changed.emit()

    def delete_selected(self) -> None:
        row = self._selected_row()
        if row is None or row.reading is None:
            return
        self._store.delete_reading(row.reading.id)
        self.refresh()
        self.readings_changed.emit()

    # --- the table --------------------------------------------------------------------

    def refresh(self) -> None:
        job_id = self._selected_job()
        job = self._store.get_job(job_id) if job_id is not None else None
        board = (job.board_id or "").strip() if job is not None else ""
        readings = self._store.list_readings(job.id) if job is not None else []
        references = self._store.reference_readings(board, exclude_job=job.id) if job else []
        self.rows = compare(readings, references)

        if job is None:
            self.board_label.setText("Select a job to record readings.")
        elif not board:
            self.board_label.setText(
                "No board number on this job. Readings are saved, but there's nothing to "
                "compare with until the job has one (Jobs → Import, or Boards)."
            )
        else:
            points = len({(r.point, r.mode) for r in references})
            self.board_label.setText(
                f"Board {board}: {points} known-good point{'s' if points != 1 else ''}."
            )
        enabled = job is not None
        for widget in (self.point_edit, self.record_button, self.mark_good_button):
            widget.setEnabled(enabled)
        self.delete_button.setEnabled(enabled and any(r.reading for r in self.rows))

        self._points.setStringList(sorted({r.point for r in self.rows}))
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            self._fill(index, row)

    def _fill(self, index: int, row: Row) -> None:
        found = mode(row.mode)
        unit = found.unit if found else ""
        point = row.point if found is None or found.key == "diode" else f"{row.point} ({unit})"
        # Values without units: the mode gives them, and the columns stay narrow.
        this = format_value(row.reading.value, row.reading.status, "") if row.reading else ""
        if row.reference_count == 0:
            good = ""
        elif row.reference_open:
            good = "OL"
        else:
            good = format_value(row.reference, None, "")
        cells = [point, this, good, row.verdict.value if row.verdict is not Verdict.NEW else ""]
        for column, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if column == 3 and row.verdict in _VERDICT_COLOURS:
                item.setForeground(QBrush(QColor(_VERDICT_COLOURS[row.verdict])))
            if row.reference_count:
                item.setToolTip(f"Known-good from {row.reference_count} reading(s)")
            self.table.setItem(index, column, item)

    def _selected_row(self) -> Row | None:
        index = self.table.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def _pick_row(self, index: int, _column: int) -> None:
        """Clicking a point fills it in, ready to measure (the checklist)."""
        if 0 <= index < len(self.rows):
            self.point_edit.setText(self.rows[index].point)
            self.point_edit.setFocus()

    def _say(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet("color: #ff7a7a;" if error else "")
