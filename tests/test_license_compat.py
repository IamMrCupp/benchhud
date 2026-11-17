"""A GPL-only Qt module needs a GPL-family project licence, and a NOTICES entry.

Most of Qt is offered under the LGPL, but some modules are GPLv3-or-commercial
only — and they ship inside the same PySide6 wheels, whose package metadata
lists the LGPL. Importing one is a licensing decision, not a free ride on a
dependency that's already there. Qt Charts got into the power graph that way
while benchhud was under a licence the GPL can't be combined with.

These tests make that decision visible: the day a GPL-only module is imported
under a non-GPL licence, or without a NOTICES entry, the suite fails.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: PySide6 module → the name Qt gives it. GPLv3-or-commercial in the open-source
#: edition, per https://doc.qt.io/qt-6/licensing.html and each module's own page.
GPL_ONLY_QT: dict[str, str] = {
    "QtCanvasPainter": "Qt Canvas Painter",
    "QtCharts": "Qt Charts",
    "QtCoap": "Qt CoAP",
    "QtDataVisualization": "Qt Data Visualization",
    "QtGraphs": "Qt Graphs",
    "QtGraphsWidgets": "Qt Graphs",
    "QtGrpc": "Qt GRPC",
    "QtHttpServer": "Qt HTTP Server",
    "QtMqtt": "Qt MQTT",
    "QtNetworkAuth": "Qt Network Authorization",
    "QtQuick3D": "Qt Quick 3D",
    "QtQuickTimeline": "Qt Quick Timeline",
    "QtVirtualKeyboard": "Qt Virtual Keyboard",
    "QtWaylandCompositor": "Qt Wayland Compositor",
}

#: Project licences a GPLv3-only module can be combined with.
GPL_FAMILY = ("AGPL-3.0", "GPL-3.0")

_QT_IMPORT = re.compile(r"\bPySide6\.(Qt\w+)")


def _gpl_only_modules_used() -> set[str]:
    used: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        used.update(_QT_IMPORT.findall(path.read_text(encoding="utf-8")))
    return used & GPL_ONLY_QT.keys()


def _project_licence() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["license"]


def test_the_power_graph_still_counts() -> None:
    # If this fails the scan has gone blind, and the two tests below pass vacuously.
    assert "QtCharts" in _gpl_only_modules_used()


def test_gpl_only_qt_modules_need_a_gpl_family_licence() -> None:
    used = _gpl_only_modules_used()
    licence = _project_licence()
    assert not used or licence.startswith(GPL_FAMILY), (
        f"{sorted(used)} are GPLv3-only Qt modules, but the project licence is {licence!r}. "
        "Either drop the import or keep benchhud under a GPL-family licence."
    )


def test_gpl_only_qt_modules_are_in_notices() -> None:
    notices = (ROOT / "NOTICES").read_text(encoding="utf-8")
    names = {GPL_ONLY_QT[module] for module in _gpl_only_modules_used()}
    missing = sorted(name for name in names if name not in notices)
    assert not missing, f"NOTICES has no entry for: {missing}"
