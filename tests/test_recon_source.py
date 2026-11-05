"""The recon seam: Null / InMemory sources, the importer, and the active ref."""

from __future__ import annotations

from benchhud.recon import (
    Artifact,
    Engagement,
    InMemoryReconSource,
    NullReconSource,
    resolve_engagement,
)
from benchhud.shell.main_window import MainWindow

_E1 = Engagement("R26-0004", title="Smart lock teardown", target_class="iot-lock", status="active")
_E2 = Engagement("R26-0005", title="ECU bring-up", target_class="automotive-ecu")


# --- NullReconSource -----------------------------------------------------


def test_null_source_is_empty() -> None:
    source = NullReconSource()
    assert source.name == "none"
    assert source.list_engagements() == []
    assert source.get_engagement("R26-0004") is None
    assert source.artifacts("R26-0004") == []


# --- InMemoryReconSource -------------------------------------------------


def test_in_memory_lists_and_gets_by_ref() -> None:
    source = InMemoryReconSource(engagements=[_E1, _E2])
    assert [e.ref for e in source.list_engagements()] == ["R26-0004", "R26-0005"]
    assert source.get_engagement("R26-0005") == _E2
    assert source.get_engagement("nope") is None


def test_in_memory_artifacts_are_scoped_by_ref() -> None:
    dump = Artifact("flash.bin", kind="firmware-dump", size=8, status="complete")
    source = InMemoryReconSource(engagements=[_E1], artifacts_by_ref={"R26-0004": [dump]})
    assert source.artifacts("R26-0004") == [dump]
    assert source.artifacts("R26-0005") == []  # an unknown ref is empty, not an error


def test_list_engagements_returns_a_copy() -> None:
    source = InMemoryReconSource(engagements=[_E1])
    source.list_engagements().append(_E2)
    assert [e.ref for e in source.list_engagements()] == ["R26-0004"]


# --- resolve_engagement (the "Import from engagement…" lookup) ------------


def test_resolve_engagement_returns_the_projection() -> None:
    source = InMemoryReconSource(engagements=[_E1])
    assert resolve_engagement(source, "R26-0004") == _E1


def test_resolve_engagement_unknown_is_none() -> None:
    assert resolve_engagement(InMemoryReconSource(), "R26-0004") is None


# --- active engagement on the window -------------------------------------


def test_window_has_no_active_engagement_by_default(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.active_engagement is None


def test_import_engagement_sets_the_active_ref(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window._recon_source = InMemoryReconSource(engagements=[_E1])

    engagement = window.import_engagement("R26-0004")

    assert engagement == _E1
    assert window.active_engagement == "R26-0004"


def test_import_engagement_unknown_ref_leaves_active_unchanged(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window._recon_source = InMemoryReconSource(engagements=[_E1])
    window.import_engagement("R26-0004")

    result = window.import_engagement("ghost")

    assert result is None
    assert window.active_engagement == "R26-0004"  # unchanged by a failed import
