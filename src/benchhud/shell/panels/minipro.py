"""T48 panel: read, back up and write chips on an XGecu T48 / TL866 with minipro (#253).

The Flash panel's twin (see :mod:`benchhud.shell.panels.flash` for the shared
rules: a backup is two matching reads, writes and erases ask first and default
to No, a wrong-size image is refused, every result goes on the job). What's
different is the chip: minipro can't detect one, so you search the part name,
pick the package, and **Check ID** confirms the chip in the socket is that part.
"""

from __future__ import annotations

import shutil
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
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from benchhud.config import MiniproConfig
from benchhud.flash import minipro
from benchhud.flash.flashrom import DumpResult, FlashError, ProcessRunner, Runner, sha256_file
from benchhud.jobs.store import JobStore
from benchhud.shell.panel import PANEL_MARGINS, Panel
from benchhud.shell.panels.flash import (
    ChooseImage,
    ChooseSave,
    Confirm,
    Executor,
    _thread_executor,
    short_hash,
)

_LOG_LINES = 2000


class MiniproPanel(Panel):
    panel_id = "minipro"
    panel_title = "T48"

    job_note_added = Signal()
    _line = Signal(str)
    _finished = Signal(str, object)

    def __init__(
        self,
        config: MiniproConfig,
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
        self.info: minipro.ChipInfo | None = None
        self._backed_up: set[str] = set()
        self._busy: str | None = None

        self.missing = QLabel()
        self.missing.setWordWrap(True)

        self.search_edit = QLineEdit(config.last_chip.split("@")[0])
        self.search_edit.setPlaceholderText("Part, e.g. W25Q64 or 24C02")
        self.search_edit.setMinimumWidth(60)
        self.search_edit.returnPressed.connect(self.search)
        self.search_button = QPushButton("Find")
        self.search_button.clicked.connect(self.search)
        self.chip = QComboBox()
        self.chip.setMinimumContentsLength(10)
        self.chip.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.chip.setPlaceholderText("Pick the package")
        self.chip.activated.connect(lambda _i: self.pick_chip())
        if config.last_chip:
            self.chip.addItem(config.last_chip)
            self.chip.setCurrentIndex(0)

        self.id_button = QPushButton("Check ID")
        self.id_button.setToolTip("Confirm the chip in the socket is the part picked")
        self.id_button.clicked.connect(self.check_id)
        self.read_button = QPushButton("Read backup")
        self.read_button.setToolTip("Read the chip twice; keep it only if both reads match")
        self.read_button.clicked.connect(self.read_backup)
        self.write_button = QPushButton("Write…")
        self.write_button.clicked.connect(self.write)
        self.erase_button = QPushButton("Erase…")
        self.erase_button.clicked.connect(self.erase)
        self.cancel_button = QPushButton("Stop")
        self.cancel_button.clicked.connect(self.cancel)

        self.status = QLabel("Find the part, pick the package, then Check ID.")
        self.status.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(_LOG_LINES)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log.setPlaceholderText("minipro's output shows here")

        find = QHBoxLayout()
        find.addWidget(self.search_edit, stretch=1)
        find.addWidget(self.search_button)
        actions = QGridLayout()
        actions.addWidget(self.id_button, 0, 0)
        actions.addWidget(self.read_button, 0, 1)
        actions.addWidget(self.write_button, 1, 0)
        actions.addWidget(self.erase_button, 1, 1)
        actions.addWidget(self.cancel_button, 2, 0, 1, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addWidget(self.missing)
        layout.addWidget(QLabel("Chip"))
        layout.addLayout(find)
        layout.addWidget(self.chip)
        layout.addLayout(actions)
        layout.addWidget(self.status)
        layout.addWidget(self.log, stretch=1)

        self._line.connect(self.log.appendPlainText)
        self._finished.connect(self._on_finished)
        self._refresh()
        if config.last_chip and self.installed():
            self.pick_chip()

    # --- state ------------------------------------------------------------------

    @property
    def command(self) -> str:
        return self._config.command.strip() or "minipro"

    def installed(self) -> bool:
        return self._which(self.command) is not None

    def _refresh(self) -> None:
        installed = self.installed()
        self.missing.setVisible(not installed)
        if not installed:
            self.missing.setText(
                f"minipro isn't installed ({self.command} isn't on PATH). Build it from "
                "gitlab.com/DavidGriffith/minipro, or set its path under [minipro] command."
            )
        idle = self._busy is None
        ready = idle and installed and self.info is not None
        for widget in (self.search_edit, self.search_button):
            widget.setEnabled(idle and installed)
        self.chip.setEnabled(idle and installed)
        for button in (self.id_button, self.read_button, self.write_button, self.erase_button):
            button.setEnabled(ready)
        self.cancel_button.setEnabled(not idle)

    def _say(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet("color: #ff7a7a;" if error else "")

    def _start(self, operation: str, job: Callable[[], Any], message: str) -> None:
        self._busy = operation
        self._say(message)
        if operation not in ("search", "info"):
            self.log.appendPlainText(f"$ {operation} {self.info.name if self.info else ''}")
        self._refresh()

        def run() -> None:
            try:
                result: object = job()
            except Exception as exc:  # shown in the panel, never raised into Qt
                result = exc
            self._finished.emit(operation, result)

        self._executor(run)

    def _emit_line(self, line: str) -> None:
        self._line.emit(line)

    # --- operations -------------------------------------------------------------

    def search(self) -> None:
        query = self.search_edit.text().strip()
        if self._busy or len(query) < 2:
            return
        self._start(
            "search",
            lambda: minipro.search(self._runner, self.command, query),
            f"Searching for {query}…",
        )

    def pick_chip(self) -> None:
        name = self.chip.currentText().strip()
        if self._busy or not name:
            return
        self.info = None
        self._start(
            "info",
            lambda: minipro.chip_info(self._runner, self.command, name),
            f"Looking up the {name}…",
        )

    def check_id(self) -> None:
        info = self.info
        if self._busy or info is None:
            return
        self._start(
            "check ID",
            lambda: minipro.check_id(self._runner, self.command, info.name, self._emit_line),
            "Reading the chip's ID…",
        )

    def read_backup(self) -> None:
        info = self.info
        if self._busy or info is None:
            return
        name = minipro.dump_name(info.name, self._now().strftime("%Y%m%d-%H%M%S"))
        directory = self._job_dir()
        dest = directory / name if directory is not None else self._choose_save(name)
        if dest is None:
            return
        self._start(
            "read",
            lambda: minipro.read_verified(
                self._runner, self.command, info.name, dest, self._emit_line
            ),
            f"Reading the {info.name} twice…",
        )

    def write(self) -> None:
        info = self.info
        if self._busy or info is None:
            return
        image = self._choose_image()
        if image is None:
            return
        try:
            minipro.check_image(info, image)
        except (FlashError, OSError) as exc:
            self._say(str(exc), error=True)
            return
        warning = (
            ""
            if info.name in self._backed_up
            else "\n\nThere's no backup of this chip from this session. Read one first "
            "unless you're sure."
        )
        if not self._confirm(
            "Write the chip?",
            f"Write {image.name} to the {info.name}? This erases what's on it.{warning}",
        ):
            return
        self._start(
            "write",
            lambda: (
                image,
                minipro.write_image(
                    self._runner, self.command, info, image, self._emit_line, sha256_file
                ),
            ),
            f"Writing {image.name} to the {info.name}…",
        )

    def erase(self) -> None:
        info = self.info
        if self._busy or info is None:
            return
        warning = "" if info.name in self._backed_up else "\n\nThere's no backup of it yet."
        if not self._confirm("Erase the chip?", f"Erase the {info.name}?{warning}"):
            return
        self._start(
            "erase",
            lambda: minipro.erase_chip(self._runner, self.command, info.name, self._emit_line),
            f"Erasing the {info.name}…",
        )

    def cancel(self) -> None:
        if self._busy is None:
            return
        if self._busy in ("write", "erase") and not self._confirm(
            "Stop minipro?", "Stopping now leaves the chip half-written. Stop anyway?"
        ):
            return
        self._runner.cancel()

    # --- results -------------------------------------------------------------------

    def _on_finished(self, operation: str, result: object) -> None:
        self._busy = None
        info = self.info
        if isinstance(result, Exception):
            self._say(str(result), error=True)
            if operation in ("write", "erase") and info is not None:
                self._note(f"T48 {operation} FAILED on the {info.name}: {result}")
        elif operation == "search":
            self._show_matches(result)  # type: ignore[arg-type]
        elif operation == "info":
            self.info = result  # type: ignore[assignment]
            assert self.info is not None
            self._config.last_chip = self.info.name
            self._say(f"{self.info.name}: {self.info.size:,} bytes. Check ID next.")
        elif operation == "check ID" and info is not None:
            check: minipro.IdCheck = result  # type: ignore[assignment]
            self._say(check.describe(info.name), error=not check.ok)
        elif operation == "read" and info is not None:
            self._read_done(info, result)  # type: ignore[arg-type]
        elif operation == "write" and info is not None:
            image, sha = result  # type: ignore[misc]
            self._say(f"Wrote and verified {image.name}.")
            self._note(
                f"T48 write: {image.name} (sha256 {short_hash(sha)}) to the {info.name}, verified."
            )
        elif operation == "erase" and info is not None:
            self._say(f"Erased the {info.name}.")
            self._note(f"T48 erase: the {info.name}.")
        self._refresh()

    def _show_matches(self, names: list[str]) -> None:
        self.chip.clear()
        self.info = None
        for name in names:
            self.chip.addItem(name)
        self.chip.setCurrentIndex(-1)  # pick the package by hand: never a guess
        if not names:
            self._say("No parts match. Try fewer characters (24C02, not AT24C02N).", error=True)
        else:
            more = " (first 200)" if len(names) >= 200 else ""
            self._say(f"{len(names)} matches{more}. Pick the exact part and package.")

    def _read_done(self, info: minipro.ChipInfo, result: DumpResult) -> None:
        if result.matched and result.path is not None:
            self._backed_up.add(info.name)
            self._say(f"Backup saved: {result.path.name}. Both reads match.")
            self._note(
                f"T48 backup: the {info.name}, two reads match. sha256 {short_hash(result.sha256)}",
                attachment=str(result.path),
            )
        else:
            self._say(
                "The two reads differ, so this isn't a backup. Reseat the chip and read "
                "again. Both reads are kept beside it.",
                error=True,
            )
            self._note(f"T48 read: the {info.name}, the two reads differ. Not a backup.")

    # --- the job ------------------------------------------------------------------------

    def _job_dir(self) -> Path | None:
        job_id = self._selected_job()
        return self._store.attachments_dir(job_id) if job_id is not None else None

    def _note(self, text: str, attachment: str | None = None) -> None:
        job_id = self._selected_job()
        if job_id is None:
            return
        self._store.add_note(job_id, text, attachment=attachment)
        self.job_note_added.emit()

    # --- dialogs ----------------------------------------------------------------------

    def _ask_save_path(self, name: str) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the backup", str(Path.home() / name), "Chip images (*.bin)"
        )
        return Path(path) if path else None

    def _ask_image(self) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Image to write", str(Path.home()), "Chip images (*.bin *.rom *.hex);;All (*)"
        )
        return Path(path) if path else None

    def _ask(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
