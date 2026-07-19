"""Tests for the telemetry snapshot reader and panel (hardware-free)."""

from __future__ import annotations

from benchhud.instruments.owon_spm8104 import from_serial_port
from benchhud.instruments.telemetry import TelemetrySnapshot, read_snapshot
from benchhud.shell.panels.telemetry import TelemetryPanel


class _FailingSerial:
    """A SerialPort whose readline raises, to exercise error handling."""

    def write(self, data: bytes) -> int:
        return len(data)

    def readline(self) -> bytes:
        raise OSError("serial disconnected")

    def close(self) -> None:
        pass


def test_read_snapshot_from_fake_owon(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial)
    snap = read_snapshot(owon)
    assert snap.ok
    assert snap.dmm is not None
    assert snap.dmm.mode == "CONT"
    assert snap.dmm.status == "OL"
    assert snap.voltage == 3.300
    assert snap.current == 0.150


def test_read_snapshot_captures_errors() -> None:
    owon = from_serial_port(_FailingSerial())
    snap = read_snapshot(owon)
    assert not snap.ok
    assert snap.error is not None


def test_panel_starts_with_placeholders(qtbot) -> None:
    panel = TelemetryPanel()
    qtbot.addWidget(panel)
    assert "—" in panel.dmm_text
    assert panel.panel_id == "telemetry"


def test_panel_shows_numeric_dmm_and_psu(qtbot, make_fake_serial) -> None:
    port = make_fake_serial(
        {
            "*IDN?": "OWON,SPM8104,1,FV:1",
            "CONFigure:ALL?": "VDC,3.301,Auto,",
            "MEAS:VOLT?": "5.000",
            "MEAS:CURR?": "0.250",
        }
    )
    panel = TelemetryPanel()
    qtbot.addWidget(panel)
    panel.update_reading(read_snapshot(from_serial_port(port)))
    assert "VDC" in panel.dmm_text
    assert "3.301" in panel.dmm_text
    assert "5.000 V" in panel.voltage_text
    assert "0.250 A" in panel.current_text


def test_panel_shows_status_token(qtbot, fake_owon_serial) -> None:
    panel = TelemetryPanel()
    qtbot.addWidget(panel)
    panel.update_reading(read_snapshot(from_serial_port(fake_owon_serial)))
    assert "OL" in panel.dmm_text


def test_panel_shows_error(qtbot) -> None:
    panel = TelemetryPanel()
    qtbot.addWidget(panel)
    panel.update_reading(TelemetrySnapshot(error="boom"))
    assert "error" in panel.dmm_text.lower()
