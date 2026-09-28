"""Bench probe: find out which OWON SPM8104 control commands really work.

The driver's reads are bench-verified; every write is modelled from standard
SCPI and marked ``UNVERIFIED`` (#164). This probe turns those guesses into facts
in one bench session, and also tries the commands GPU-rail work wants next —
setpoint read-back (``VOLT?``/``CURR?``), hardware OVP/OCP (``VOLT:LIM``/
``CURR:LIM``), the CV/CC state word (``MEAS:ALL:INFO?``) and the two competing
DMM-mode syntaxes (``FUNC:…`` as used by the rebadged Multicomp unit, versus the
``CONFigure:…`` the driver currently sends).

    uv run python -m benchhud.instruments.owon_probe /dev/ttyUSB0
    uv run python -m benchhud.instruments.owon_probe /dev/ttyUSB0 --energize

**Run it with nothing on the output terminals and the DMM leads disconnected.**
The output is forced off first and last, and is only ever enabled with
``--energize`` — at 1 V / 0.1 A, current limit sent before voltage, both before
``OUTP ON``. Setpoints and limits are restored where the firmware lets us read
the originals. Two things are left changed, and the report says so: the DMM mode
(we can't know the command for a mode we didn't set), and after ``--energize``
the setpoints stay at 1 V / 0.1 A — the safe direction to be wrong in.

The probe deliberately talks to a bare :class:`SerialPort` rather than the
driver: the driver's control gate and ceilings exist to protect boards, and a
tool whose whole job is poking unverified commands should not need them switched
off in config to run.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from benchhud.instruments.serial_port import SerialPort

PROBE_VOLTS = 1.0
PROBE_AMPS = 0.1
PROBE_OVP = 6.0
TOLERANCE = 0.02

#: Read-only queries worth knowing about, tried verbatim.
CANDIDATE_QUERIES: tuple[str, ...] = (
    "OUTP?",
    "VOLT?",
    "CURR?",
    "VOLT:LIM?",
    "CURR:LIM?",
    "MEAS:POW?",
    "MEAS:ALL:INFO?",
    "FUNC?",
)

#: DMM mode switches to try, per target, in order. The firmware may be literal
#: about long vs short forms, so ``CONFigure:`` and ``CONF:`` are both tried.
DMM_CANDIDATES: dict[str, tuple[str, ...]] = {
    "dc-volts": ("FUNC:VOLT:DC", "CONFigure:VOLT:DC", "CONF:VOLT:DC"),
    "continuity": ("FUNC:CONT", "CONFigure:CONT", "CONF:CONT"),
}


class ProbeAbortedError(Exception):
    """The probe stopped early — wrong device, or the line protocol lost sync."""


@dataclass(frozen=True)
class ProbeResult:
    """One finding. ``verdict`` is ``ok``, ``no-response``, ``mismatch`` or ``info``."""

    name: str
    command: str
    response: str
    verdict: str
    note: str = ""


@dataclass
class ProbeReport:
    identity: str = ""
    results: list[ProbeResult] = field(default_factory=list)
    aborted: str | None = None

    def add(self, name: str, command: str, response: str, verdict: str, note: str = "") -> None:
        self.results.append(ProbeResult(name, command, response, verdict, note))


class _Wire:
    """Line I/O that keeps request and response paired on an unknown dialect.

    A command the firmware doesn't know may answer with nothing, or with an error
    line we didn't expect. Either would leave the *next* query reading the wrong
    line. So after every write, and after every unanswered query, we send
    ``*IDN?`` — the one command verified to answer — and read until its reply
    shows up. Anything that arrives first was unsolicited, and is reported.
    """

    def __init__(self, port: SerialPort, settle: float) -> None:
        self._port = port
        self._settle = settle
        self.identity = ""

    def _send(self, command: str) -> None:
        self._port.write((command + "\n").encode("ascii"))

    def _readline(self) -> str:
        return self._port.readline().decode("ascii", errors="replace").strip()

    def identify(self) -> str:
        self._send("*IDN?")
        self.identity = self._readline()
        return self.identity

    def sync(self) -> list[str]:
        """Re-pair the line; return any unsolicited lines that came first."""
        self._send("*IDN?")
        extra: list[str] = []
        for _ in range(4):
            line = self._readline()
            if line == self.identity:
                return extra
            if line:
                extra.append(line)
        raise ProbeAbortedError(f"lost sync with the instrument (saw {extra!r})")

    def write(self, command: str) -> list[str]:
        """Send a command that expects no reply; return unsolicited lines."""
        self._send(command)
        if self._settle:
            time.sleep(self._settle)
        return self.sync()

    def query(self, command: str) -> str:
        self._send(command)
        response = self._readline()
        if not response:
            self.sync()
        return response


def _as_float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE


def _probe_setting(
    wire: _Wire, report: ProbeReport, name: str, command: str, query: str, value: float
) -> None:
    """Write ``command value``, read it back with ``query``, restore the original."""
    original = _as_float(wire.query(query))
    sent = f"{command} {value:.3f}"
    extra = wire.write(sent)
    note = f"unsolicited reply: {extra!r}" if extra else ""
    if original is None:
        report.add(name, sent, "", "no-response", f"{query} unanswered — confirm on the panel")
        return
    readback = wire.query(query)
    got = _as_float(readback)
    verdict = "ok" if got is not None and _close(got, value) else "mismatch"
    wire.write(f"{command} {original:.3f}")
    restored = _as_float(wire.query(query))
    if restored is None or not _close(restored, original):
        note = (note + " " if note else "") + f"NOT restored: wanted {original}, has {restored}"
    report.add(name, sent, readback, verdict, note)


def _dmm_mode(wire: _Wire) -> str:
    return wire.query("CONFigure:ALL?").split(",")[0].strip()


def _probe_dmm(wire: _Wire, report: ProbeReport) -> None:
    original = _dmm_mode(wire)
    # Pick a target we aren't already in, so "the mode changed" means something.
    target = "continuity" if "VOLT" in original.upper() else "dc-volts"
    for command in DMM_CANDIDATES[target]:
        wire.write(command)
        now = _dmm_mode(wire)
        if now and now != original:
            report.add(
                f"dmm-mode:{target}",
                command,
                now,
                "ok",
                f"DMM left in {now!r} (was {original!r}) — restore it on the panel",
            )
            return
        report.add(f"dmm-mode:{target}", command, now, "no-response", "mode did not change")


def _probe_energize(wire: _Wire, report: ProbeReport, sleep: Callable[[float], None]) -> None:
    """Enable the output at 1 V / 0.1 A, measure, drop it. Limit goes first."""
    try:
        wire.write(f"CURR {PROBE_AMPS:.3f}")
        wire.write(f"VOLT {PROBE_VOLTS:.3f}")
        wire.write("OUTP ON")
        sleep(0.5)
        measured = wire.query("MEAS:VOLT?")
        got = _as_float(measured)
        verdict = "ok" if got is not None and abs(got - PROBE_VOLTS) <= 0.1 else "mismatch"
        report.add("output-on", "OUTP ON", measured, verdict, "MEAS:VOLT? with the output enabled")
        report.add("state-word-on", "MEAS:ALL:INFO?", wire.query("MEAS:ALL:INFO?"), "info")
        report.add("output-query-on", "OUTP?", wire.query("OUTP?"), "info")
    finally:
        wire.write("OUTP OFF")


def run_probe(
    port: SerialPort,
    *,
    energize: bool = False,
    settle: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
) -> ProbeReport:
    """Probe the instrument on ``port``. Never raises; failures land in the report."""
    report = ProbeReport()
    wire = _Wire(port, settle)
    identity = wire.identify()
    report.identity = identity
    if "OWON" not in identity.upper():
        # Don't spray guessed commands at something that isn't the OWON.
        report.aborted = f"not an OWON (*IDN? → {identity!r}); nothing else was sent"
        return report

    try:
        wire.write("OUTP OFF")
        for query in CANDIDATE_QUERIES:
            response = wire.query(query)
            report.add("query", query, response, "ok" if response else "no-response")
        _probe_setting(wire, report, "set-current", "CURR", "CURR?", PROBE_AMPS)
        _probe_setting(wire, report, "set-voltage", "VOLT", "VOLT?", PROBE_VOLTS)
        _probe_setting(wire, report, "set-ovp", "VOLT:LIM", "VOLT:LIM?", PROBE_OVP)
        _probe_dmm(wire, report)
        if energize:
            _probe_energize(wire, report, sleep)
    except ProbeAbortedError as exc:
        report.aborted = str(exc)
    except Exception as exc:
        # A yanked cable or a serial error is a finding, not a traceback.
        report.aborted = f"serial failure: {exc}"
    finally:
        # Best effort and unconditional: the rail must not outlive the probe.
        try:
            port.write(b"OUTP OFF\n")
        except Exception as exc:
            report.aborted = f"{report.aborted or ''} could not send final OUTP OFF: {exc}".strip()
    return report


def format_report(report: ProbeReport) -> str:
    """Plain-text table, ready to paste into research.md."""
    lines = [f"identity: {report.identity}"]
    if report.aborted:
        lines.append(f"ABORTED: {report.aborted}")
    for r in report.results:
        lines.append(f"{r.verdict:<11} {r.name:<22} {r.command:<22} -> {r.response!r}")
        if r.note:
            lines.append(f"{'':<11} {r.note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("device", help="serial device, e.g. /dev/ttyUSB0")
    parser.add_argument(
        "--energize",
        action="store_true",
        help="also enable the output at 1 V / 0.1 A (nothing may be connected)",
    )
    args = parser.parse_args(argv)

    print("Nothing on the output terminals, DMM leads disconnected.")
    if input("Type 'yes' to continue: ").strip().lower() != "yes":
        return 1

    import serial

    port = serial.Serial(args.device, baudrate=115200, timeout=1.0)
    try:
        report = run_probe(port, energize=args.energize)
    finally:
        port.close()
    print(format_report(report))
    return 1 if report.aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())
