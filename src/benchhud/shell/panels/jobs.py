"""Jobs panel — track board-repair jobs on the bench.

A list of jobs (newest first), the selected job's details, and its note
timeline. Backed by :class:`~benchhud.jobs.store.JobStore`; the store is injected
so tests run against an in-memory database.

This is the surface a later "capture to job" action hooks into — appending the
current scope/thermal frame and live meter readings as timeline entries.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
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

from benchhud.boards.library import BoardFile, BoardLibrary
from benchhud.jobs.capture import CaptureContext, capture_to_job
from benchhud.jobs.report import export_report
from benchhud.jobs.store import Job, JobStatus, JobStore
from benchhud.shell.panel import PANEL_MARGINS, Panel
from benchhud.shell.qt_util import fit_combo_popup, grow_fields
from benchhud.shell.ticket_picker import TicketPickerDialog
from benchhud.tickets.importer import import_ticket
from benchhud.tickets.source import Ticket, TicketSource

CaptureProvider = Callable[[], CaptureContext | None]
TicketSourceProvider = Callable[[], TicketSource]
BoardLibraryProvider = Callable[[], BoardLibrary]
OpenBoardFn = Callable[[Path], bool]


def preferred_board_file(files: list[BoardFile]) -> BoardFile | None:
    """Pick which format to hand OpenBoardView — its native .obv first."""
    for fmt in ("obv", "brd", "bdv"):
        for board in files:
            if board.format == fmt:
                return board
    return files[0] if files else None


def _short_stamp(iso: str) -> str:
    """``2026-07-22T03:37:53+00:00`` → ``2026-07-22 03:37`` (keeps notes narrow)."""
    return iso[:16].replace("T", " ")


def _slug(text: str) -> str:
    """A filename-safe slug for the export default name (dashes collapsed)."""
    out: list[str] = []
    for char in text.lower():
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "job"


class JobsPanel(Panel):
    panel_id = "jobs"
    panel_title = "Jobs"

    #: The selected job changed, or its fields were saved. Carries nothing:
    #: listeners read what they need (the browser-source job card, #180).
    job_changed = Signal()

    def __init__(
        self,
        store: JobStore,
        capture_provider: CaptureProvider | None = None,
        ticket_source: TicketSourceProvider | None = None,
        board_library: BoardLibraryProvider | None = None,
        open_board: OpenBoardFn | None = None,
        open_schematic: OpenBoardFn | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._capture_provider = capture_provider
        self._ticket_source = ticket_source
        self._board_library = board_library
        self._open_board = open_board
        self._open_schematic = open_schematic
        self._board_files: list[BoardFile] = []
        self._schematic_files: list[BoardFile] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addLayout(self._build_new_row())
        layout.addWidget(self.job_list_widget())
        layout.addWidget(self._build_detail())
        self.history_label = QLabel()
        self.history_label.setObjectName("JobHistoryHint")
        self.history_label.setWordWrap(True)
        self.history_label.setVisible(False)
        layout.addWidget(self.history_label)
        layout.addWidget(self._build_board_row())
        layout.addWidget(QLabel("Notes"))
        layout.addWidget(self._build_notes())

        self.refresh_jobs()

    # --- construction ----------------------------------------------------

    def _build_new_row(self) -> QHBoxLayout:
        self.new_device_edit = QLineEdit()
        self.new_device_edit.setPlaceholderText("Device / board…")
        self.new_device_edit.returnPressed.connect(self.create_job)
        self.new_device_edit.setMinimumWidth(60)
        self.new_button = QPushButton("New job")
        self.new_button.clicked.connect(self.create_job)
        self.import_button = QPushButton("Import…")
        self.import_button.setToolTip("Import an open ticket from intake onto the bench")
        self.import_button.clicked.connect(self.import_from_ticket)
        # Only useful when a ticket source is wired in.
        self.import_button.setVisible(self._ticket_source is not None)

        row = QHBoxLayout()
        row.addWidget(self.new_device_edit, stretch=1)
        row.addWidget(self.new_button)
        row.addWidget(self.import_button)
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
        self.status_combo.setMinimumContentsLength(8)
        fit_combo_popup(self.status_combo)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_current)
        self.export_button = QPushButton("Export report…")
        self.export_button.setToolTip("Save a customer-facing repair report (Markdown)")
        self.export_button.clicked.connect(self.export_report)

        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addStretch(1)
        buttons.addWidget(self.export_button)

        form = QFormLayout()
        grow_fields(form)
        form.addRow("Device", self.device_edit)
        form.addRow("Serial", self.serial_edit)
        form.addRow("Customer", self.customer_edit)
        form.addRow("Symptoms", self.symptoms_edit)
        form.addRow("Status", self.status_combo)
        # The buttons span the panel: under the field column they set the
        # panel's minimum width past a narrow dock's (#230).
        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 0, 0, 0)
        box.addLayout(form)
        box.addLayout(buttons)
        return widget

    def _build_notes(self) -> QWidget:
        self.notes_list = QListWidget()
        self.notes_list.setMinimumHeight(80)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Add a note…")
        self.note_edit.returnPressed.connect(self.add_note)
        self.note_edit.setMinimumWidth(60)
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
        # Its own row: beside the note field it made the panel wider than the dock.
        capture_row = QHBoxLayout()
        capture_row.addStretch(1)
        capture_row.addWidget(self.capture_button)

        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.notes_list)
        box.addLayout(row)
        box.addLayout(capture_row)
        return widget

    def _build_board_row(self) -> QWidget:
        self.board_label = QLabel()
        self.board_label.setObjectName("JobBoardHint")
        self.board_label.setWordWrap(True)
        self.open_board_button = QPushButton("Open board")
        self.open_board_button.setToolTip("Open this board in the configured viewer")
        self.open_board_button.clicked.connect(self.open_board_for_selected)
        self.open_schematic_button = QPushButton("Open schematic")
        self.open_schematic_button.setToolTip("Open this board's schematic PDF")
        self.open_schematic_button.clicked.connect(self.open_schematic_for_selected)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.open_board_button)
        buttons.addWidget(self.open_schematic_button)
        row = QVBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.board_label)
        row.addLayout(buttons)
        widget = QWidget()
        widget.setLayout(row)
        self._board_row = widget
        widget.setVisible(False)
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

    def import_from_ticket(self) -> TicketPickerDialog | None:
        """Open the intake queue, let the operator pick a ticket, import it."""
        if self._ticket_source is None:
            return None
        tickets = self._ticket_source().list_open_tickets()
        dialog = TicketPickerDialog(tickets, parent=self)
        dialog.accepted.connect(lambda: self._import_selected(dialog))
        dialog.open()
        return dialog

    def _import_selected(self, dialog: TicketPickerDialog) -> None:
        ticket = dialog.selected_ticket()
        if ticket is not None:
            self.import_ticket_onto_bench(ticket)

    def import_ticket_onto_bench(self, ticket: Ticket) -> Job:
        """Create (or reselect) the local job for ``ticket`` and focus it.

        Idempotent via :func:`import_ticket` — re-importing the same ticket
        reselects the existing job rather than duplicating it.
        """
        job = import_ticket(self._store, ticket)
        self.refresh_jobs()
        self.select_job(job.id)
        return job

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

    def export_report(self) -> None:
        """Prompt for a path and write the selected job's repair report."""
        job_id = self.selected_job_id()
        if job_id is None:
            return
        job = self._store.get_job(job_id)
        suggested = f"repair-report-{_slug(job.device) if job else job_id}.md"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export repair report", suggested, "Markdown (*.md)"
        )
        if path:
            self.write_report(job_id, path)

    def write_report(self, job_id: int, path: str) -> bool:
        """Render the job's report to ``path``. Returns False if it doesn't exist."""
        markdown = export_report(
            self._store, job_id, generated_at=datetime.now(UTC).isoformat(timespec="seconds")
        )
        if markdown is None:
            return False
        Path(path).write_text(markdown, encoding="utf-8")
        return True

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
            self._update_history(None)
            self._update_board(None)
            self.job_changed.emit()
            return
        self.device_edit.setText(job.device)
        self.serial_edit.setText(job.serial)
        self.customer_edit.setText(job.customer)
        self.symptoms_edit.setPlainText(job.symptoms)
        self.status_combo.setCurrentText(job.status)
        self.refresh_notes()
        self._update_history(job)
        self._update_board(job)
        self.job_changed.emit()

    def _update_board(self, job: Job | None) -> None:
        """Show the job's board, its formats, and whether a schematic is filed."""
        board_id = (job.board_id or "").strip() if job is not None else ""
        if not board_id:
            self._board_files = []
            self._schematic_files = []
            self._board_row.setVisible(False)
            return
        library = self._board_library() if self._board_library is not None else None
        self._board_files = library.resolve(board_id) if library is not None else []
        self._schematic_files = library.resolve_schematic(board_id) if library is not None else []
        self._board_row.setVisible(True)

        has_schematic = bool(self._schematic_files)
        self.open_schematic_button.setEnabled(has_schematic and self._open_schematic is not None)
        if self._board_files:
            formats = ", ".join(sorted({b.format for b in self._board_files}))
            schematic = " · 📄 schematic" if has_schematic else ""
            self.board_label.setText(f"▦ Board {board_id} — in library ({formats}){schematic}")
            self.open_board_button.setEnabled(self._open_board is not None)
        elif has_schematic:
            # No boardview, but the schematic is here — still worth surfacing.
            self.board_label.setText(f"▦ Board {board_id} — schematic only, no boardview")
            self.open_board_button.setEnabled(False)
        else:
            self.board_label.setText(f"▦ Board {board_id} — not in library")
            self.open_board_button.setEnabled(False)

    def open_board_for_selected(self) -> bool:
        """Open the selected job's board view. False when there's nothing to open."""
        board = preferred_board_file(self._board_files)
        if board is None or self._open_board is None:
            return False
        return self._open_board(board.path)

    def open_schematic_for_selected(self) -> bool:
        """Open the selected job's schematic PDF. False when there's none."""
        if not self._schematic_files or self._open_schematic is None:
            return False
        return self._open_schematic(self._schematic_files[0].path)

    def _update_history(self, job: Job | None) -> None:
        """Show a 'seen before' hint when prior jobs match this board."""
        related = self._store.find_related(job) if job is not None else []
        if not related:
            self.history_label.clear()
            self.history_label.setVisible(False)
            return
        basis = "serial" if job.serial.strip() else "model"
        ids = ", ".join(f"#{j.id}" for j in related[:5])
        more = "…" if len(related) > 5 else ""
        plural = "job" if len(related) == 1 else "jobs"
        self.history_label.setText(
            f"⟲ Seen before — {len(related)} prior {plural} for this {basis} ({ids}{more})."
        )
        self.history_label.setToolTip(
            "\n".join(f"#{j.id}  {j.device} — {j.status}" for j in related)
        )
        self.history_label.setVisible(True)
