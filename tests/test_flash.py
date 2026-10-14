"""flashrom driving and the Flash panel (#227), against a fake flashrom."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

from benchhud.config import Config, FlashConfig
from benchhud.flash.flashrom import (
    Chip,
    FlashError,
    ProcessRunner,
    check_image,
    dump_name,
    parse_chips,
    probe,
    read_verified,
    write_image,
)
from benchhud.jobs.store import JobStore
from benchhud.shell.panels.flash import FlashPanel

W25Q64 = Chip("Winbond", "W25Q64BV/W25Q64CV/W25Q64FV", 8192)
SMALL = Chip("Winbond", "W25X10", 1)  # 1 kB keeps the fake dumps small

PROBE_ONE = """flashrom v1.4.0 on Linux 6.8.0 (x86_64)
Using clock_gettime for delay loops (clk_id: 1, resolution: 1ns).
Found Winbond flash chip "W25X10" (1 kB, SPI) on ch341a_spi.
No operations were specified."""

PROBE_MANY = """Found Macronix flash chip "MX25L6405" (8192 kB, SPI) on ch341a_spi.
Found Macronix flash chip "MX25L6405D" (8192 kB, SPI) on ch341a_spi.
Multiple flash chip definitions match the detected chip(s): "MX25L6405", "MX25L6405D"
Please specify which chip definition to use with the -c <chipname> option."""


class FakeFlashrom:
    """Plays flashrom: answers probes, writes dumps for -r, records every call."""

    def __init__(self, probe_output: str = PROBE_ONE, reads: list[bytes] | None = None) -> None:
        self.probe_output = probe_output
        self.reads = list(reads or [b"\x01" * 1024, b"\x01" * 1024])
        self.calls: list[list[str]] = []
        self.exit_code = 0
        self.cancelled = False

    def run(self, argv, on_line) -> int:
        self.calls.append(list(argv))
        if "-r" in argv:
            Path(argv[argv.index("-r") + 1]).write_bytes(self.reads.pop(0))
            on_line("Reading flash... done.")
        elif "-w" in argv:
            on_line("Erasing and writing flash chip... Erase/write done.")
            on_line("Verifying flash... VERIFIED.")
        elif "-E" in argv:
            on_line("Erasing flash chip... Erase/write done.")
        else:
            for line in self.probe_output.splitlines():
                on_line(line)
        return self.exit_code

    def cancel(self) -> None:
        self.cancelled = True


# --- parsing and the operations ---------------------------------------------------


def test_parse_one_chip() -> None:
    assert parse_chips(PROBE_ONE) == [Chip("Winbond", "W25X10", 1, "SPI")]


def test_parse_ambiguous_chips() -> None:
    assert [c.name for c in parse_chips(PROBE_MANY)] == ["MX25L6405", "MX25L6405D"]


def test_parse_nothing_found() -> None:
    assert parse_chips("No EEPROM/flash device found.\nNote: flashrom can never write...") == []


def test_probe_passes_the_programmer_and_streams_output() -> None:
    fake, lines = FakeFlashrom(), []
    chips = probe(fake, "flashrom", "ch341a_spi", lines.append)
    assert fake.calls == [["flashrom", "-p", "ch341a_spi"]]
    assert chips[0].name == "W25X10" and lines[-1] == "No operations were specified."


def test_dump_names_are_filename_safe() -> None:
    assert dump_name(W25Q64, "20261014-101500") == "W25Q64BV_W25Q64CV_W25Q64FV-20261014-101500.bin"


def test_matching_reads_become_the_backup(tmp_path) -> None:
    fake = FakeFlashrom()
    dest = tmp_path / "W25X10.bin"
    result = read_verified(fake, "flashrom", "ch341a_spi", SMALL, dest, lambda _l: None)
    assert result.matched and result.path == dest and dest.read_bytes() == b"\x01" * 1024
    assert sorted(p.name for p in tmp_path.iterdir()) == ["W25X10.bin", "W25X10.bin.sha256"]
    assert (tmp_path / "W25X10.bin.sha256").read_text().startswith(result.sha256)
    assert all(call[3:5] == ["-c", "W25X10"] for call in fake.calls)


def test_differing_reads_are_not_a_backup(tmp_path) -> None:
    fake = FakeFlashrom(reads=[b"\x01" * 1024, b"\x02" * 1024])
    dest = tmp_path / "W25X10.bin"
    result = read_verified(fake, "flashrom", "ch341a_spi", SMALL, dest, lambda _l: None)
    assert not result.matched and result.path is None and not dest.exists()
    assert result.reads is not None and all(p.exists() for p in result.reads)  # kept for a look


def test_a_failed_read_raises(tmp_path) -> None:
    fake = FakeFlashrom()
    fake.exit_code = 1
    with pytest.raises(FlashError, match="couldn't read"):
        read_verified(fake, "flashrom", "p", SMALL, tmp_path / "x.bin", lambda _l: None)


def test_a_wrong_size_image_is_refused_before_anything_runs(tmp_path) -> None:
    image = tmp_path / "bios.bin"
    image.write_bytes(b"\xff" * 512)
    fake = FakeFlashrom()
    with pytest.raises(FlashError, match="512 bytes"):
        write_image(fake, "flashrom", "p", SMALL, image, lambda _l: None)
    assert fake.calls == []


def test_write_passes_the_image_and_returns_its_hash(tmp_path) -> None:
    image = tmp_path / "bios.bin"
    image.write_bytes(b"\xff" * 1024)
    fake = FakeFlashrom()
    sha = write_image(fake, "flashrom", "p", SMALL, image, lambda _l: None)
    assert fake.calls == [["flashrom", "-p", "p", "-c", "W25X10", "-w", str(image)]]
    assert len(sha) == 64
    check_image(SMALL, image)  # the right size passes


def test_process_runner_streams_a_real_process() -> None:
    lines: list[str] = []
    code = ProcessRunner().run([sys.executable, "-c", "print('a'); print('b')"], lines.append)
    assert code == 0 and lines == ["a", "b"]


def test_process_runner_reports_a_missing_program() -> None:
    with pytest.raises(FlashError, match="couldn't run"):
        ProcessRunner().run(["/nonexistent/flashrom"], lambda _l: None)


# --- the panel ------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    yield store
    store.close()


def _panel(qtbot, store, fake, *, job=None, answers=(), image=None, save=None):
    asked: list[str] = []
    replies = list(answers)

    def confirm(title, text):
        asked.append(text)
        return replies.pop(0) if replies else False

    panel = FlashPanel(
        FlashConfig(),
        store,
        lambda: job,
        runner=fake,
        executor=lambda run: run(),  # synchronous: the result signal fires in place
        which=lambda _c: "/usr/bin/flashrom",
        choose_save=lambda name: save / name if save else None,
        choose_image=lambda: image,
        confirm=confirm,
        now=lambda: datetime(2026, 10, 14, 10, 15, 0),
    )
    qtbot.addWidget(panel)
    return panel, asked


def test_nothing_runs_until_a_chip_is_detected(qtbot, store) -> None:
    panel, _ = _panel(qtbot, store, FakeFlashrom())
    assert panel.detect_button.isEnabled()
    assert not panel.read_button.isEnabled() and not panel.write_button.isEnabled()


def test_missing_flashrom_says_how_to_get_it(qtbot, store) -> None:
    panel = FlashPanel(FlashConfig(), store, lambda: None, which=lambda _c: None)
    qtbot.addWidget(panel)
    assert "isn't installed" in panel.missing.text()
    assert not panel.detect_button.isEnabled()


def test_detect_then_back_up_to_the_job(qtbot, store) -> None:
    job = store.create_job("ThinkPad T480").id
    fake = FakeFlashrom()
    panel, _ = _panel(qtbot, store, fake, job=job)
    notes: list[None] = []
    panel.job_note_added.connect(lambda: notes.append(None))

    panel.detect()
    assert panel.selected_chip == SMALL and panel.read_button.isEnabled()
    panel.read_backup()

    dump = store.attachments_dir(job) / "W25X10-20261014-101500.bin"
    assert dump.exists()
    [note] = store.list_notes(job)
    assert "two reads match" in note.text and note.attachment == str(dump)
    assert notes and "Backup saved" in panel.status.text()
    assert "Reading flash... done." in panel.log.toPlainText()


def test_a_mismatch_is_logged_and_not_called_a_backup(qtbot, store) -> None:
    job = store.create_job("board").id
    fake = FakeFlashrom(reads=[b"\x01" * 1024, b"\x02" * 1024])
    panel, asked = _panel(qtbot, store, fake, job=job)
    panel.detect()
    panel.read_backup()
    assert "differ" in panel.status.text()
    assert "differ" in store.list_notes(job)[0].text
    # and so a write still warns there's no backup
    panel._choose_image = lambda: None
    panel.write()
    assert asked == []  # no image picked, nothing asked


def test_without_a_job_it_asks_where_to_save(qtbot, store, tmp_path) -> None:
    panel, _ = _panel(qtbot, store, FakeFlashrom(), save=tmp_path)
    panel.detect()
    panel.read_backup()
    assert (tmp_path / "W25X10-20261014-101500.bin").exists()


def test_ambiguous_chips_must_be_picked(qtbot, store) -> None:
    panel, _ = _panel(qtbot, store, FakeFlashrom(PROBE_MANY))
    panel.detect()
    assert panel.chip.count() == 2 and panel.chip.isEnabled()
    assert "Pick the one printed on the chip" in panel.status.text()
    assert panel.selected_chip is None and not panel.read_button.isEnabled()  # no guessing
    panel.chip.setCurrentIndex(1)
    assert panel.selected_chip.name == "MX25L6405D" and panel.read_button.isEnabled()


def test_no_chip_says_check_the_clip(qtbot, store) -> None:
    panel, _ = _panel(qtbot, store, FakeFlashrom("No EEPROM/flash device found."))
    panel.detect()
    assert "Check the clip" in panel.status.text() and not panel.read_button.isEnabled()


def test_write_asks_first_and_no_means_nothing_runs(qtbot, store, tmp_path) -> None:
    image = tmp_path / "bios.bin"
    image.write_bytes(b"\xff" * 1024)
    fake = FakeFlashrom()
    panel, asked = _panel(qtbot, store, fake, image=image, answers=[False])
    panel.detect()
    panel.write()
    assert "no backup of this chip" in asked[0]
    assert not any("-w" in call for call in fake.calls)


def test_write_after_a_backup_logs_the_hash(qtbot, store, tmp_path) -> None:
    job = store.create_job("board").id
    image = tmp_path / "bios.bin"
    image.write_bytes(b"\xff" * 1024)
    fake = FakeFlashrom()
    panel, asked = _panel(qtbot, store, fake, job=job, image=image, answers=[True])
    panel.detect()
    panel.read_backup()
    panel.write()
    assert "no backup" not in asked[0]
    assert any("-w" in call for call in fake.calls)
    assert "Flash write: bios.bin" in store.list_notes(job)[-1].text
    assert "verified" in store.list_notes(job)[-1].text


def test_a_wrong_size_image_never_reaches_the_confirm(qtbot, store, tmp_path) -> None:
    image = tmp_path / "bios.bin"
    image.write_bytes(b"\xff" * 10)
    panel, asked = _panel(qtbot, store, FakeFlashrom(), image=image, answers=[True])
    panel.detect()
    panel.write()
    assert asked == [] and "Wrong image, or wrong chip" in panel.status.text()


def test_a_failed_write_is_logged_to_the_job(qtbot, store, tmp_path) -> None:
    job = store.create_job("board").id
    image = tmp_path / "bios.bin"
    image.write_bytes(b"\xff" * 1024)
    fake = FakeFlashrom()
    panel, _ = _panel(qtbot, store, fake, job=job, image=image, answers=[True])
    panel.detect()
    fake.exit_code = 1
    panel.write()
    assert "FAILED" in store.list_notes(job)[-1].text
    assert "half-written" in panel.status.text()


def test_stopping_a_write_asks_first(qtbot, store) -> None:
    fake = FakeFlashrom()
    panel, asked = _panel(qtbot, store, fake, answers=[False, True])
    panel._busy = "write"
    panel.cancel()
    assert not fake.cancelled and "half-written" in asked[0]
    panel.cancel()
    assert fake.cancelled


def test_the_programmer_is_remembered(qtbot, store) -> None:
    config = Config()
    panel = FlashPanel(config.flash, store, lambda: None, which=lambda _c: None)
    qtbot.addWidget(panel)
    panel.programmer.setCurrentText("linux_spi:dev=/dev/spidev0.0")
    assert config.flash.programmer == "linux_spi:dev=/dev/spidev0.0"
    assert Config.from_dict(config.to_dict()).flash.programmer == "linux_spi:dev=/dev/spidev0.0"
