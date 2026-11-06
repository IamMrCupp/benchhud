"""The capture/firmware-dump status panel — artifact metadata by ref, off air."""

from __future__ import annotations

from benchhud.recon import Artifact, InMemoryReconSource, NullReconSource
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.capture import CapturePanel, human_size

_DUMP = Artifact("flash.bin", kind="firmware-dump", size=8 * 1024 * 1024, status="complete")
_TRACE = Artifact("can.log", kind="capture", size=512, status="in-progress")


def _source(ref="R26-1", artifacts=(_DUMP, _TRACE)):
    return InMemoryReconSource(artifacts_by_ref={ref: list(artifacts)})


def _panel(qtbot, source, ref="R26-1") -> CapturePanel:
    panel = CapturePanel(lambda: source, active_ref=lambda: ref)
    qtbot.addWidget(panel)
    return panel


# --- sensitivity (todo 19) -----------------------------------------------


def test_capture_panel_is_sensitive() -> None:
    assert CapturePanel.sensitive is True


# --- size formatting -----------------------------------------------------


def test_human_size_is_compact() -> None:
    assert human_size(0) == "0 B"
    assert human_size(512) == "512 B"
    assert human_size(8 * 1024 * 1024) == "8.0 MB"
    assert human_size(3 * 1024**3) == "3.0 GB"


# --- behaviour -----------------------------------------------------------


def test_lists_the_artifacts_for_the_active_engagement(qtbot) -> None:
    panel = _panel(qtbot, _source())
    assert panel.tree.topLevelItemCount() == 2
    first = panel.tree.topLevelItem(0)
    assert first.text(0) == "flash.bin"
    assert first.text(1) == "firmware-dump"
    assert first.text(2) == "8.0 MB"
    assert first.text(3) == "complete"


def test_only_the_active_engagements_artifacts_show(qtbot) -> None:
    panel = _panel(qtbot, _source(ref="R26-1"), ref="R26-OTHER")
    assert panel.tree.topLevelItemCount() == 0
    assert "No captures recorded for R26-OTHER" in panel.message_label.text()


def test_no_engagement_prompts_import(qtbot) -> None:
    panel = _panel(qtbot, _source(), ref=None)
    assert "Import an engagement" in panel.message_label.text()


def test_no_backend_is_said(qtbot) -> None:
    panel = _panel(qtbot, NullReconSource())
    assert "No recon backend" in panel.message_label.text()


# --- window wiring -------------------------------------------------------


def test_window_builds_the_capture_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window.capture_panel, CapturePanel)
    assert window.capture_panel.sensitive is True
