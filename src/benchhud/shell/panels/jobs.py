"""Jobs panel — track board-repair jobs on the bench.

A list of jobs (newest first), the selected job's details, and its note
timeline. Backed by :class:`~benchhud.jobs.store.JobStore`; the store is injected
so tests run against an in-memory database.

This is the surface a later "capture to job" action hooks into — appending the
current scope/thermal frame and live meter readings as timeline entries.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from benchhud.jobs.capture import CaptureContext, capture_to_job
from benchhud.jobs.store import JobStatus, JobStore
from benchhud.shell.panel import Panel
from benchhud.shell.qt_util import fit_combo_popup

CaptureProvider = Callable[[], CaptureContext | None]


def _short_stamp(iso: str) -> str:
    """``2026-07-22T03:37:53+00:00`` → ``2026-07-22 03:37`` (keeps notes narrow)."""
    return iso[:16].replace("T", " ")


class JobsPanel(Panel):
    panel_id = "jobs"
    panel_title = "Jobs"

    def __init__(
        self,
        store: JobStore,
        capture_provider: CaptureProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._capture_provider = capture_provider

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_new_row())
        layout.addWidget(self.job_list_widget())
        layout.addWidget(self._build_detail())
        layout.addWidget(QLabel("Notes"))
        layout.addWidget(self._build_notes())

        self.refresh_jobs()

    # --- construction ----------------------------------------------------

    def _build_new_row(self) -> QHBoxLayout:
        self.new_device_edit = QLineEdit()
        self.new_device_edit.setPlaceholderText("Device / board…")
        self.new_device_edit.returnPressed.connect(self.create_job)
        self.new_button = QPushButton("New job")
        self.new_button.clicked.connect(self.create_job)

        row = QHBoxLayout()
        row.addWidget(self.new_device_edit, stretch=1)
        row.addWidget(self.new_button)
        return row

    def job_list_widget(self) -> QListWidget:
        self.job_list = QListWidget()
        self.job_list.setMinimumHeight(90)
        self.job_list.currentItemChanged.connect(lambda *_: self._load_selected())
        return self.job_list

    def _build_detail(self) -> QWidget:
        self.device_edit = QLineEdit()
        self.serial_edit = QLineEdit()
        self.customer_edit = QLineEdit()
        self.symptoms_edit = QPlainTextEdit()
        self.symptoms_edit.setMaximumHeight(60)
        self.status_combo = QComboBox()
        self.status_combo.addItems([s.value for s in JobStatus])
        fit_combo_popup(self.status_combo)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_current)

        widget = QWidget()
        form = QFormLayout(widget)
        form.addRow("Device", self.device_edit)
        form.addRow("Serial", self.serial_edit)
        form.addRow("Customer", self.customer_edit)
        form.addRow("Symptoms", self.symptoms_edit)
        form.addRow("Status", self.status_combo)
        form.addRow("", self.save_button)
        return widget

    def _build_notes(self) -> QWidget:
        self.notes_list = QListWidget()
        self.notes_list.setMinimumHeight(80)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Add a note…")
        self.note_edit.returnPressed.connect(self.add_note)
        self.add_note_button = QPushButton("Add note")
        self.add_note_button.clicked.connect(self.add_note)
        self.capture_button = QPushButton("Capture bench")
        self.capture_button.setToolTip(
            "Attach the current scope + thermal frames and live meter readings"
        )
        self.capture_button.clicked.connect(self.capture_bench)

        row = QHBoxLayout()
        row.addWidget(self.note_edit, stretch=1)
        row.addWidget(self.add_note_button)
        row.addWidget(self.capture_button)

        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.notes_list)
        box.addLayout(row)
        return widget

    # --- actions ---------------------------------------------------------

    def create_job(self) -> None:
        """Create a job from the device box (ignored when empty)."""
        device = self.new_device_edit.text().strip()
        if not device:
            return
        job = self._store.create_job(device)
        self.new_device_edit.clear()
        self.refresh_jobs()
        self.select_job(job.id)

    def save_current(self) -> None:
        """Write the detail fields back to the selected job."""
        job_id = self.selected_job_id()
        if job_id is None:
            return
        self._store.update_job(
            job_id,
            device=self.device_edit.text().strip(),
            serial=self.serial_edit.text().strip(),
            customer=self.customer_edit.text().strip(),
            symptoms=self.symptoms_edit.toPlainText().strip(),
            status=self.status_combo.currentText(),
        )
        self.refresh_jobs()
        self.select_job(job_id)

    def add_note(self) -> None:
        """Append a note to the selected job (ignored when empty/none selected)."""
        job_id = self.selected_job_id()
        text = self.note_edit.text().strip()
        if job_id is None or not text:
            return
        self._store.add_note(job_id, text)
        self.note_edit.clear()
        self.refresh_notes()

    def capture_bench(self) -> None:
        """Attach the current scope/thermal frames + meter readings to the job."""
        job_id = self.selected_job_id()
        if job_id is None or self._capture_provider is None:
            return
        context = self._capture_provider()
        if context is None:
            return
        capture_to_job(self._store, job_id, context)
        self.refresh_notes()

    # --- state -----------------------------------------------------------

    def refresh_jobs(self) -> None:
        self.job_list.blockSignals(True)
        self.job_list.clear()
        for job in self._store.list_jobs():
            item = QListWidgetItem(f"#{job.id}  {job.device} — {job.status}")
            item.setData(Qt.ItemDataRole.UserRole, job.id)
            self.job_list.addItem(item)
        self.job_list.blockSignals(False)
        self._load_selected()

    def refresh_notes(self) -> None:
        self.notes_list.clear()
        job_id = self.selected_job_id()
        if job_id is None:
            return
        for note in self._store.list_notes(job_id):
            self.notes_list.addItem(f"{_short_stamp(note.created_at)}  {note.text}")

    def selected_job_id(self) -> int | None:
        item = self.job_list.currentItem()
        return None if item is None else int(item.data(Qt.ItemDataRole.UserRole))

    def select_job(self, job_id: int) -> None:
        for row in range(self.job_list.count()):
            item = self.job_list.item(row)
            if int(item.data(Qt.ItemDataRole.UserRole)) == job_id:
                self.job_list.setCurrentRow(row)
                return

    def _load_selected(self) -> None:
        job_id = self.selected_job_id()
        job = self._store.get_job(job_id) if job_id is not None else None
        if job is None:
            for edit in (self.device_edit, self.serial_edit, self.customer_edit):
                edit.clear()
            self.symptoms_edit.clear()
            self.notes_list.clear()
            return
        self.device_edit.setText(job.device)
        self.serial_edit.setText(job.serial)
        self.customer_edit.setText(job.customer)
        self.symptoms_edit.setPlainText(job.symptoms)
        self.status_combo.setCurrentText(job.status)
        self.refresh_notes()
