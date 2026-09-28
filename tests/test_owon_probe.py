"""Tests for the OWON bench probe, against a stateful fake of the instrument.

The fake is parameterised by *dialect*, because the whole point of the probe is
that we don't know which one the real firmware speaks.
"""

from __future__ import annotations

from benchhud.instruments.owon_probe import format_report, run_probe

IDN = "OWON,SPM8104,25390427,FV:V1.0.0"


class FakeOwon:
    """A stateful SPM8104 stand-in implementing the ``SerialPort`` protocol."""

    def __init__(
        self,
        *,
        idn: str = IDN,
        setpoint_queries: bool = True,
        dmm_prefix: str = "FUNC:",
        error_on_unknown: bool = False,
        answers_idn_after: int | None = None,
    ) -> None:
        self.idn = idn
        self.setpoint_queries = setpoint_queries
        self.dmm_prefix = dmm_prefix
        self.error_on_unknown = error_on_unknown
        self.answers_idn_after = answers_idn_after
        self.state = {"VOLT": 12.5, "CURR": 3.0, "VOLT:LIM": 13.0, "CURR:LIM": 8.5}
        self.output = False
        self.dmm_mode = "CONT"
        self.written: list[str] = []
        self._rx: list[str] = []

    # --- SerialPort ------------------------------------------------------

    def write(self, data: bytes) -> int:
        line = data.decode("ascii").rstrip("\n")
        self.written.append(line)
        reply = self._handle(line)
        if reply is not None:
            self._rx.append(reply)
        return len(data)

    def readline(self) -> bytes:
        return (self._rx.pop(0) + "\n").encode("ascii") if self._rx else b""

    def close(self) -> None:
        pass

    # --- instrument behaviour --------------------------------------------

    def _handle(self, line: str) -> str | None:
        if line == "*IDN?":
            if self.answers_idn_after is not None and len(self.written) > self.answers_idn_after:
                return None
            return self.idn
        if line == "OUTP ON":
            self.output = True
            return None
        if line == "OUTP OFF":
            self.output = False
            return None
        if line == "OUTP?":
            return "1" if self.output else "0"
        if line == "CONFigure:ALL?":
            return f"{self.dmm_mode},OL,Manual,"
        if line == "MEAS:VOLT?":
            return f"{self.state['VOLT'] if self.output else 0.0:.3f}"
        if line.startswith(self.dmm_prefix) and "?" not in line:
            self.dmm_mode = line.removeprefix(self.dmm_prefix).split(":")[0]
            return None
        name, _, arg = line.partition(" ")
        if name in self.state and arg:
            self.state[name] = float(arg)
            return None
        if line.endswith("?") and line[:-1] in self.state and self.setpoint_queries:
            return f"{self.state[line[:-1]]:.3f}"
        return "ERR" if self.error_on_unknown else None


def _by_name(report, name):
    return [r for r in report.results if r.name == name]


def test_refuses_to_poke_a_device_that_is_not_the_owon() -> None:
    port = FakeOwon(idn="RIGOL,DP832")
    report = run_probe(port, settle=0)
    assert report.aborted and "not an OWON" in report.aborted
    assert port.written == ["*IDN?"]


def test_output_is_forced_off_first_and_last_and_never_enabled_by_default() -> None:
    port = FakeOwon()
    run_probe(port, settle=0)
    commands = [w for w in port.written if w != "*IDN?"]
    assert commands[0] == "OUTP OFF"
    assert commands[-1] == "OUTP OFF"
    assert "OUTP ON" not in port.written


def test_setpoints_are_verified_by_readback_and_restored() -> None:
    port = FakeOwon()
    report = run_probe(port, settle=0)
    assert _by_name(report, "set-voltage")[0].verdict == "ok"
    assert _by_name(report, "set-current")[0].verdict == "ok"
    assert _by_name(report, "set-ovp")[0].verdict == "ok"
    assert port.state == {"VOLT": 12.5, "CURR": 3.0, "VOLT:LIM": 13.0, "CURR:LIM": 8.5}


def test_firmware_without_setpoint_queries_is_reported_not_guessed() -> None:
    port = FakeOwon(setpoint_queries=False)
    report = run_probe(port, settle=0)
    result = _by_name(report, "set-voltage")[0]
    assert result.verdict == "no-response"
    assert "panel" in result.note
    assert report.aborted is None


def test_detects_the_func_dmm_dialect() -> None:
    report = run_probe(FakeOwon(dmm_prefix="FUNC:"), settle=0)
    working = [r for r in _by_name(report, "dmm-mode:dc-volts") if r.verdict == "ok"]
    assert [r.command for r in working] == ["FUNC:VOLT:DC"]


def test_detects_the_configure_dmm_dialect() -> None:
    report = run_probe(FakeOwon(dmm_prefix="CONFigure:"), settle=0)
    attempts = _by_name(report, "dmm-mode:dc-volts")
    assert [(r.command, r.verdict) for r in attempts] == [
        ("FUNC:VOLT:DC", "no-response"),
        ("CONFigure:VOLT:DC", "ok"),
    ]


def test_targets_continuity_when_the_dmm_is_already_on_volts() -> None:
    port = FakeOwon()
    port.dmm_mode = "VOLT"
    report = run_probe(port, settle=0)
    assert _by_name(report, "dmm-mode:continuity")[0].verdict == "ok"


def test_unsolicited_error_lines_do_not_desync_later_queries() -> None:
    port = FakeOwon(error_on_unknown=True, dmm_prefix="CONFigure:")
    report = run_probe(port, settle=0)
    assert report.aborted is None
    # The reading after the noisy writes is still paired with its own query.
    assert _by_name(report, "set-voltage")[0].verdict == "ok"


def test_energize_sends_limit_then_voltage_then_on_and_always_drops_the_rail() -> None:
    port = FakeOwon()
    report = run_probe(port, energize=True, settle=0, sleep=lambda _s: None)
    w = port.written
    last_dmm_read = len(w) - 1 - w[::-1].index("CONFigure:ALL?")
    energize_phase = [c for c in w[last_dmm_read:] if c.startswith(("CURR ", "VOLT ", "OUTP O"))]
    assert energize_phase[:4] == ["CURR 0.100", "VOLT 1.000", "OUTP ON", "OUTP OFF"]
    assert port.output is False
    assert _by_name(report, "output-on")[0].verdict == "ok"


def test_rail_is_dropped_even_when_the_energized_measurement_blows_up() -> None:
    port = FakeOwon()

    def boom(_seconds: float) -> None:
        raise OSError("cable yanked")

    report = run_probe(port, energize=True, settle=0, sleep=boom)
    assert port.output is False
    assert report.aborted and "cable yanked" in report.aborted


def test_lost_sync_aborts_and_still_sends_output_off() -> None:
    port = FakeOwon(answers_idn_after=3)
    report = run_probe(port, settle=0)
    assert report.aborted and "lost sync" in report.aborted
    assert port.written[-1] == "OUTP OFF"


def test_report_is_pasteable_text() -> None:
    text = format_report(run_probe(FakeOwon(), settle=0))
    assert text.startswith(f"identity: {IDN}")
    assert "set-voltage" in text
