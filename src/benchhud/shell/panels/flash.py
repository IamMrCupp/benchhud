"""Flash panel: read, back up and write SPI flash chips with flashrom (#227).

The work is in :mod:`benchhud.flash.flashrom`; this is the bench around it:

* **Detect first.** Every operation names the chip flashrom found, so a write
  can't go to a chip nobody looked at.
* **A backup is two matching reads,** saved with the selected job when there
  is one. A mismatch says to reseat the clip, and nothing counts as a backup.
* **Write and Erase ask first,** defaulting to No, and say when there's no
  backup of that chip yet. An image of the wrong size is refused before
  anything runs.
* **Every result lands on the job's timeline:** chip, programmer, file, hash.

flashrom runs on a worker thread; its output and the result come back over
Qt signals. The executor, runner and dialogs are injectable, so tests run
synchronously and never start a process.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from benchhud.config import FlashConfig
from benchhud.flash.flashrom import (
    PROGRAMMERS,
    Chip,
    DumpResult,
    FlashError,
    ProcessRunner,
    Runner,
    check_image,
    dump_name,
    erase_chip,
    probe,
    read_verified,
    write_image,
)
from benchhud.jobs.store import JobStore
from benchhud.shell.panel import PANEL_MARGINS, Panel

#: Runs a job off the GUI thread.
Executor = Callable[[Callable[[], None]], None]
#: (default file name) -> where to save a dump, or None to cancel.
ChooseSave = Callable[[str], Path | None]
#: () -> the image to write, or None to cancel.
ChooseImage = Callable[[], Path | None]
#: (title, text) -> go ahead?
Confirm = Callable[[str, str], bool]

_LOG_LINES = 2000


def _thread_executor(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="flashrom", daemon=True).start()


def short_hash(sha256: str) -> str:
    return f"{sha256[:16]}…"


class FlashPanel(Panel):
    panel_id = "flash"
    panel_title = "Flash"

    #: A note went onto the selected job's timeline.
    job_note_added = Signal()
    _line = Signal(str)
    _finished = Signal(str, object)  # (operation, result or exception)

    def __init__(
        self,
        config: FlashConfig,
        store: JobStore,
        selected_job: Callable[[], int | None],
        *,
        runner: Runner | None = None,
        executor: Executor = _thread_executor,
        which: Callable[[str], str | None] = shutil.which,
        choose_save: ChooseSave | None = None,
        choose_image: ChooseImage | None = None,
        confirm: Confirm | None = None,
        now: Callable[[], datetime] = datetime.now,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._store = store
        self._selected_job = selected_job
        self._runner = runner or ProcessRunner()
        self._executor = executor
        self._which = which
        self._choose_save = choose_save or self._ask_save_path
        self._choose_image = choose_image or self._ask_image
        self._confirm = confirm or self._ask
        self._now = now
        self._chips: list[Chip] = []
        self._backed_up: set[str] = set()  # chips with a verified dump this session
        self._busy: str | None = None

        self.missing = QLabel()
        self.missing.setObjectName("FlashMissing")
        self.missing.setWordWrap(True)

        self.programmer = QComboBox()
        self.programmer.setEditable(True)
        self.programmer.setMinimumContentsLength(8)
        self.programmer.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        for label, value in PROGRAMMERS:
            self.programmer.addItem(value)
            self.programmer.setItemData(self.programmer.count() - 1, label)
        self.programmer.setCurrentText(config.programmer)
        self.programmer.setToolTip("flashrom's -p value. Type any other programmer.")
        self.programmer.currentTextChanged.connect(self._remember_programmer)
        self.detect_button = QPushButton("Detect")
        self.detect_button.setToolTip("Ask the programmer which chip is on the clip")
        self.detect_button.clicked.connect(self.detect)

        self.chip = QComboBox()
        self.chip.setMinimumContentsLength(8)
        self.chip.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.chip.setPlaceholderText("Detect a chip first")
        self.chip.currentIndexChanged.connect(lambda _i: self._refresh())

        self.read_button = QPushButton("Read backup")
        self.read_button.setToolTip("Read the chip twice; keep it only if both reads match")
        self.read_button.clicked.connect(self.read_backup)
        self.write_button = QPushButton("Write…")
        self.write_button.setToolTip("Write an image to the chip, then verify it")
        self.write_button.clicked.connect(self.write)
        self.erase_button = QPushButton("Erase…")
        self.erase_button.clicked.connect(self.erase)
        self.cancel_button = QPushButton("Stop")
        self.cancel_button.setToolTip("Stop flashrom")
        self.cancel_button.clicked.connect(self.cancel)

        self.status = QLabel("Clip the chip, pick the programmer, then Detect.")
        self.status.setObjectName("FlashStatus")
        self.status.setWordWrap(True)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(_LOG_LINES)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log.setPlaceholderText("flashrom's output shows here")

        # Labels above their fields, not beside: side by side was wider than a
        # narrow dock.
        grid = QGridLayout()
        grid.addWidget(QLabel("Programmer"), 0, 0, 1, 2)
        grid.addWidget(self.programmer, 1, 0)
        grid.addWidget(self.detect_button, 1, 1)
        grid.addWidget(QLabel("Chip"), 2, 0, 1, 2)
        grid.addWidget(self.chip, 3, 0, 1, 2)
        grid.setColumnStretch(0, 1)

        actions = QGridLayout()
        actions.addWidget(self.read_button, 0, 0)
        actions.addWidget(self.write_button, 0, 1)
        actions.addWidget(self.erase_button, 1, 0)
        actions.addWidget(self.cancel_button, 1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addWidget(self.missing)
        layout.addLayout(grid)
        layout.addLayout(actions)
        layout.addWidget(self.status)
        layout.addWidget(self.log, stretch=1)

        self._line.connect(self.log.appendPlainText)
        self._finished.connect(self._on_finished)
        self._refresh()

    # --- state --------------------------------------------------------------

    @property
    def command(self) -> str:
        return self._config.command.strip() or "flashrom"

    @property
    def selected_chip(self) -> Chip | None:
        index = self.chip.currentIndex()
        return self._chips[index] if 0 <= index < len(self._chips) else None

    def installed(self) -> bool:
        return self._which(self.command) is not None

    def _remember_programmer(self, text: str) -> None:
        self._config.programmer = text.strip()

    def _refresh(self) -> None:
        installed = self.installed()
        self.missing.setVisible(not installed)
        if not installed:
            self.missing.setText(
                f"flashrom isn't installed ({self.command} isn't on PATH). Install it "
                "(apt install flashrom, brew install flashrom), or set its path under "
                "[flash] command."
            )
        idle = self._busy is None
        chip = self.selected_chip is not None
        self.programmer.setEnabled(idle)
        self.detect_button.setEnabled(idle and installed)
        self.chip.setEnabled(idle and len(self._chips) > 1)
        for button in (self.read_button, self.write_button, self.erase_button):
            button.setEnabled(idle and installed and chip)
        self.cancel_button.setEnabled(not idle)

    def _say(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet("color: #ff7a7a;" if error else "")

    def _start(self, operation: str, job: Callable[[], Any], message: str) -> None:
        self._busy = operation
        self._say(message)
        self.log.appendPlainText(f"$ {operation}")
        self._refresh()

        def run() -> None:
            try:
                result: object = job()
            except Exception as exc:  # reported in the panel, never raised into Qt
                result = exc
            self._finished.emit(operation, result)

        self._executor(run)

    def _emit_line(self, line: str) -> None:
        self._line.emit(line)

    # --- operations ---------------------------------------------------------

    def detect(self) -> None:
        if self._busy:
            return
        programmer = self._config.programmer or "ch341a_spi"
        self._start(
            "detect",
            lambda: probe(self._runner, self.command, programmer, self._emit_line),
            f"Detecting on {programmer}…",
        )

    def read_backup(self) -> None:
        chip = self.selected_chip
        if self._busy or chip is None:
            return
        name = dump_name(chip, self._now().strftime("%Y%m%d-%H%M%S"))
        directory = self._job_dir()
        dest = directory / name if directory is not None else self._choose_save(name)
        if dest is None:
            return
        programmer = self._config.programmer
        self._start(
            "read",
            lambda: read_verified(
                self._runner, self.command, programmer, chip, dest, self._emit_line
            ),
            f"Reading the {chip.name} twice…",
        )

    def write(self) -> None:
        chip = self.selected_chip
        if self._busy or chip is None:
            return
        image = self._choose_image()
        if image is None:
            return
        try:
            check_image(chip, image)
        except (FlashError, OSError) as exc:
            self._say(str(exc), error=True)
            return
        warning = (
            ""
            if chip.name in self._backed_up
            else "\n\nThere's no backup of this chip from this session. Read one first "
            "unless you're sure."
        )
        if not self._confirm(
            "Write the chip?",
            f"Write {image.name} to the {chip}? This erases what's on it.{warning}",
        ):
            return
        programmer = self._config.programmer
        self._start(
            "write",
            lambda: (
                image,
                write_image(self._runner, self.command, programmer, chip, image, self._emit_line),
            ),
            f"Writing {image.name} to the {chip.name}…",
        )

    def erase(self) -> None:
        chip = self.selected_chip
        if self._busy or chip is None:
            return
        warning = "" if chip.name in self._backed_up else "\n\nThere's no backup of it yet."
        if not self._confirm("Erase the chip?", f"Erase the {chip}?{warning}"):
            return
        programmer = self._config.programmer
        self._start(
            "erase",
            lambda: erase_chip(self._runner, self.command, programmer, chip, self._emit_line),
            f"Erasing the {chip.name}…",
        )

    def cancel(self) -> None:
        if self._busy is None:
            return
        if self._busy in ("write", "erase") and not self._confirm(
            "Stop flashrom?",
            "Stopping now leaves the chip half-written. Stop anyway?",
        ):
            return
        self._runner.cancel()

    # --- results ------------------------------------------------------------

    def _on_finished(self, operation: str, result: object) -> None:
        self._busy = None
        chip = self.selected_chip
        programmer = self._config.programmer
        if isinstance(result, Exception):
            self._say(str(result), error=True)
            if operation in ("write", "erase") and chip is not None:
                self._note(f"Flash {operation} FAILED on the {chip} via {programmer}: {result}")
        elif operation == "detect":
            self._show_chips(result)  # type: ignore[arg-type]
        elif operation == "read" and chip is not None:
            self._read_done(chip, programmer, result)  # type: ignore[arg-type]
        elif operation == "write" and chip is not None:
            image, sha = result  # type: ignore[misc]
            self._say(f"Wrote and verified {image.name}.")
            self._note(
                f"Flash write: {image.name} (sha256 {short_hash(sha)}) to the {chip} "
                f"via {programmer}, verified."
            )
        elif operation == "erase" and chip is not None:
            self._say(f"Erased the {chip.name}.")
            self._note(f"Flash erase: the {chip} via {programmer}.")
        self._refresh()

    def _show_chips(self, chips: list[Chip]) -> None:
        self._chips = list(chips)
        self.chip.clear()
        for chip in self._chips:
            self.chip.addItem(str(chip))
        # One chip is selected for you; several must be picked by hand, so a
        # read or write never runs against a guess.
        self.chip.setCurrentIndex(0 if len(self._chips) == 1 else -1)
        if not chips:
            self._say(
                "No chip answered. Check the clip's seating and pin 1, and the programmer.",
                error=True,
            )
        elif len(chips) == 1:
            self._say(f"Found the {chips[0]}.")
        else:
            self._say(f"{len(chips)} chip definitions match. Pick the one printed on the chip.")

    def _read_done(self, chip: Chip, programmer: str, result: DumpResult) -> None:
        if result.matched and result.path is not None:
            self._backed_up.add(chip.name)
            self._say(f"Backup saved: {result.path.name}. Both reads match.")
            self._note(
                f"Flash backup: the {chip} via {programmer}, two reads match. "
                f"sha256 {short_hash(result.sha256)}",
                attachment=str(result.path),
            )
        else:
            self._say(
                "The two reads differ, so this isn't a backup. Reseat the clip and read "
                "again. Both reads are kept beside it for a look.",
                error=True,
            )
            self._note(
                f"Flash read: the {chip} via {programmer}, the two reads differ. Not a backup."
            )

    # --- the job --------------------------------------------------------------

    def _job_dir(self) -> Path | None:
        job_id = self._selected_job()
        if job_id is None:
            return None
        return self._store.attachments_dir(job_id)

    def _note(self, text: str, attachment: str | None = None) -> None:
        job_id = self._selected_job()
        if job_id is None:
            return
        self._store.add_note(job_id, text, attachment=attachment)
        self.job_note_added.emit()

    # --- dialogs ----------------------------------------------------------------

    def _ask_save_path(self, name: str) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the backup", str(Path.home() / name), "Flash images (*.bin)"
        )
        return Path(path) if path else None

    def _ask_image(self) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Image to write", str(Path.home()), "Flash images (*.bin *.rom);;All files (*)"
        )
        return Path(path) if path else None

    def _ask(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,  # Enter doesn't write a chip
        )
        return answer == QMessageBox.StandardButton.Yes
