"""minipro and the T48 panel (#253), against output captured on the bench."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from benchhud.config import Config, MiniproConfig
from benchhud.flash import minipro
from benchhud.flash.flashrom import FlashError
from benchhud.jobs.store import JobStore
from benchhud.shell.panels.minipro import MiniproPanel

BANNER = [
    "Found T48 00.1.03 (0x103)",
    "Warning: T48 support is not yet complete!",
    "Warning: Firmware is out of date.",
    "  Expected  01.1.39 (0x127)",
    "  Found     00.1.03 (0x103)",
]
INFO = {
    "W25Q64BV@SOIC8": 8388608,
    "AT24C02@SOIC8": 256,
}


class FakeMinipro:
    """Plays minipro 0.7.4 on a T48: search, info, ID, read, write, erase."""

    def __init__(self, chip_id: str | None = "0xEF4017", reads=None) -> None:
        self.chip_id = chip_id  # None: an empty socket
        self.reads = list(reads or [b"\x5a" * 256, b"\x5a" * 256])
        self.calls: list[list[str]] = []
        self.exit_code = 0
        self.cancelled = False

    def run(self, argv, on_line) -> int:
        self.calls.append(list(argv))
        for line in BANNER:
            on_line(line)
        if "-L" in argv:
            query = argv[argv.index("-L") + 1].upper()
            names = ["BW25Q64A@SOP8", "W25Q64BV", "W25Q64BV@SOIC8", "AT24C02", "AT24C02@SOIC8"]
            for name in names:
                if query in name:
                    on_line(name)
            return 0
        if "-d" in argv:
            name = argv[argv.index("-d") + 1]
            if name not in INFO:
                on_line(f"Unknown device {name}")
                return 1
            on_line("---------------Chip Info----------------")
            on_line(f"Name: {name}")
            on_line(f"Memory: {INFO[name]} Bytes")
            return 0
        if "-D" in argv:
            if self.chip_id != "0xEF4017":
                on_line(f"Chip ID mismatch: expected 0xEF4017, got {self.chip_id or '0x0000'}")
                return 1
            on_line("Chip ID: 0xEF4017  OK")
            return 0
        if "-r" in argv:
            Path(argv[argv.index("-r") + 1]).write_bytes(self.reads.pop(0))
            on_line("Reading Code...  0.25Sec  OK")
        elif "-w" in argv:
            on_line("Writing Code...  1.20Sec  OK")
            on_line("Reading Code...  0.25Sec  OK")
            on_line("Verification OK")
        elif "-E" in argv:
            on_line("Erasing... 0.10Sec OK")
        return self.exit_code

    def cancel(self) -> None:
        self.cancelled = True


# --- the core ---------------------------------------------------------------------


def test_search_drops_minipros_chatter() -> None:
    assert minipro.search(FakeMinipro(), "minipro", "W25Q64") == [
        "BW25Q64A@SOP8",
        "W25Q64BV",
        "W25Q64BV@SOIC8",
    ]
    assert minipro.search(FakeMinipro(), "minipro", "x") == []  # too short to search


def test_chip_info_gives_the_size() -> None:
    assert minipro.chip_info(FakeMinipro(), "minipro", "AT24C02@SOIC8").size == 256
    with pytest.raises(FlashError, match="doesn't know"):
        minipro.chip_info(FakeMinipro(), "minipro", "NOPE")


def test_id_check_three_ways() -> None:
    name = "W25Q64BV@SOIC8"
    ok = minipro.check_id(FakeMinipro(), "minipro", name, lambda _l: None)
    assert ok.ok and "answers as" in ok.describe(name)
    empty = minipro.check_id(FakeMinipro(chip_id=None), "minipro", name, lambda _l: None)
    assert not empty.ok and "No chip answered" in empty.describe(name)
    wrong = minipro.check_id(FakeMinipro(chip_id="0xC22017"), "minipro", name, lambda _l: None)
    assert "Wrong part picked" in wrong.describe(name) and wrong.got == "0xC22017"


def test_read_twice_and_write(tmp_path) -> None:
    fake = FakeMinipro()
    dest = tmp_path / "AT24C02_SOIC8.bin"
    result = minipro.read_verified(fake, "minipro", "AT24C02@SOIC8", dest, lambda _l: None)
    assert result.matched and dest.read_bytes() == b"\x5a" * 256
    info = minipro.ChipInfo("AT24C02@SOIC8", 256)
    with pytest.raises(FlashError, match="Wrong image"):
        minipro.check_image(info, _image(tmp_path, 128))
    sha = minipro.write_image(
        fake, "minipro", info, _image(tmp_path, 256), lambda _l: None, lambda _p: "abc"
    )
    assert sha == "abc" and fake.calls[-1][:4] == ["minipro", "-p", "AT24C02@SOIC8", "-w"]


def _image(tmp_path, size: int) -> Path:
    image = tmp_path / f"img{size}.bin"
    image.write_bytes(b"\xff" * size)
    return image


# --- the panel --------------------------------------------------------------------


def _panel(qtbot, store, fake, *, job=None, answers=(), image=None, config=None):
    replies = list(answers)
    asked: list[str] = []

    def confirm(_title, text):
        asked.append(text)
        return replies.pop(0) if replies else False

    panel = MiniproPanel(
        config or MiniproConfig(),
        store,
        lambda: job,
        runner=fake,
        executor=lambda run: run(),
        which=lambda _c: "/usr/local/bin/minipro",
        choose_image=lambda: image,
        confirm=confirm,
        now=lambda: datetime(2026, 10, 15, 9, 0, 0),
    )
    qtbot.addWidget(panel)
    return panel, asked


@pytest.fixture
def store(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    yield store
    store.close()


def _pick(panel, query: str, name: str) -> None:
    panel.search_edit.setText(query)
    panel.search()
    panel.chip.setCurrentIndex(panel.chip.findText(name))
    panel.pick_chip()


def test_search_never_guesses_the_package(qtbot, store) -> None:
    panel, _ = _panel(qtbot, store, FakeMinipro())
    panel.search_edit.setText("W25Q64")
    panel.search()
    assert panel.chip.count() == 3 and panel.chip.currentIndex() == -1
    assert not panel.read_button.isEnabled()


def test_pick_check_and_back_up_to_the_job(qtbot, store) -> None:
    job = store.create_job("router", board_id="RT-AX58U").id
    config = MiniproConfig()
    panel, _ = _panel(qtbot, store, FakeMinipro(), job=job, config=config)
    _pick(panel, "24C02", "AT24C02@SOIC8")
    assert panel.info.size == 256 and config.last_chip == "AT24C02@SOIC8"
    panel.check_id()
    assert "answers as" in panel.status.text()
    panel.read_backup()
    dump = store.attachments_dir(job) / "AT24C02_SOIC8-20261015-090000.bin"
    assert dump.exists()
    assert "T48 backup: the AT24C02@SOIC8, two reads match" in store.list_notes(job)[0].text


def test_an_empty_socket_says_what_to_check(qtbot, store) -> None:
    panel, _ = _panel(qtbot, store, FakeMinipro(chip_id=None))
    _pick(panel, "W25Q64", "W25Q64BV@SOIC8")
    panel.check_id()
    assert "No chip answered" in panel.status.text()


def test_write_asks_and_refuses_the_wrong_size(qtbot, store, tmp_path) -> None:
    fake = FakeMinipro()
    panel, asked = _panel(qtbot, store, fake, image=_image(tmp_path, 100), answers=[True])
    _pick(panel, "24C02", "AT24C02@SOIC8")
    panel.write()
    assert asked == [] and "Wrong image" in panel.status.text()
    panel._choose_image = lambda: _image(tmp_path, 256)
    panel.write()
    assert "no backup of this chip" in asked[0]
    assert any("-w" in call for call in fake.calls)


def test_the_last_chip_is_looked_up_on_start(qtbot, store) -> None:
    panel, _ = _panel(qtbot, store, FakeMinipro(), config=MiniproConfig(last_chip="W25Q64BV@SOIC8"))
    assert panel.info is not None and panel.info.size == 8388608
    assert panel.read_button.isEnabled()


def test_missing_minipro(qtbot, store) -> None:
    panel = MiniproPanel(MiniproConfig(), store, lambda: None, which=lambda _c: None)
    qtbot.addWidget(panel)
    assert "isn't installed" in panel.missing.text() and not panel.search_button.isEnabled()


def test_config_round_trip() -> None:
    back = Config.from_dict({"minipro": {"command": "/opt/minipro", "last_chip": "X@DIP8"}})
    assert back.minipro.command == "/opt/minipro" and back.minipro.last_chip == "X@DIP8"
