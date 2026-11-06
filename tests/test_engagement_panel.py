"""The engagement panel and its picker — offscreen, no network."""

from __future__ import annotations

from benchhud.recon import Engagement, InMemoryReconSource, NullReconSource
from benchhud.shell.engagement_picker import EngagementPickerDialog
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.engagement import EngagementPanel

_E1 = Engagement("R26-0004", title="Smart lock teardown", target_class="iot-lock", status="active")
_E2 = Engagement("R26-0005", title="ECU bring-up", target_class="automotive-ecu", status="scoping")


class _Active:
    """Stand-in for the window's active-engagement ref."""

    def __init__(self) -> None:
        self.ref: str | None = None


def _panel(qtbot, source, active: _Active) -> EngagementPanel:
    def on_activate(ref: str) -> Engagement | None:
        engagement = source.get_engagement(ref)
        if engagement is not None:
            active.ref = engagement.ref
        return engagement

    panel = EngagementPanel(lambda: source, active_ref=lambda: active.ref, on_activate=on_activate)
    qtbot.addWidget(panel)
    return panel


# --- the sensitivity tag (todo 19) ---------------------------------------


def test_engagement_panel_is_sensitive() -> None:
    assert EngagementPanel.sensitive is True


# --- empty / no backend --------------------------------------------------


def test_no_backend_shows_placeholders_and_hides_import(qtbot) -> None:
    panel = _panel(qtbot, NullReconSource(), _Active())
    assert panel.ref_label.text() == "—"
    assert panel.import_button.isHidden() is True
    assert "No recon backend" in panel.message_label.text()


def test_backend_but_no_active_engagement_prompts_import(qtbot) -> None:
    panel = _panel(qtbot, InMemoryReconSource(engagements=[_E1]), _Active())
    assert panel.import_button.isHidden() is False
    assert "No engagement imported" in panel.message_label.text()


# --- showing the active engagement ---------------------------------------


def test_active_engagement_populates_the_projection(qtbot) -> None:
    active = _Active()
    active.ref = "R26-0004"
    panel = _panel(qtbot, InMemoryReconSource(engagements=[_E1]), active)
    assert panel.ref_label.text() == "R26-0004"
    assert panel.title_label.text() == "Smart lock teardown"
    assert panel.target_label.text() == "iot-lock"
    assert panel.status_label.text() == "active"
    assert panel.message_label.isVisible() is False


def test_active_ref_that_no_longer_resolves_says_so(qtbot) -> None:
    active = _Active()
    active.ref = "R26-9999"
    panel = _panel(qtbot, InMemoryReconSource(engagements=[_E1]), active)
    assert panel.ref_label.text() == "—"
    assert "not found" in panel.message_label.text()


# --- import flow ---------------------------------------------------------


def test_activating_a_picked_engagement_sets_active_and_displays(qtbot) -> None:
    source = InMemoryReconSource(engagements=[_E1, _E2])
    active = _Active()
    panel = _panel(qtbot, source, active)

    dialog = EngagementPickerDialog([_E1, _E2], parent=panel)
    qtbot.addWidget(dialog)
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))  # pick E2
    panel._activate_selected(dialog)

    assert active.ref == "R26-0005"
    assert panel.ref_label.text() == "R26-0005"
    assert panel.target_label.text() == "automotive-ecu"


# --- picker --------------------------------------------------------------


def test_picker_returns_none_until_a_row_is_selected(qtbot) -> None:
    dialog = EngagementPickerDialog([_E1], parent=None)
    qtbot.addWidget(dialog)
    assert dialog.selected_engagement() is None
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    assert dialog.selected_engagement() == _E1


# --- window wiring -------------------------------------------------------


def test_window_builds_the_engagement_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window.engagement_panel, EngagementPanel)
    assert window.engagement_panel.sensitive is True


def test_window_import_engagement_reflects_on_the_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window._recon_source = InMemoryReconSource(engagements=[_E1])

    window.import_engagement("R26-0004")
    window.engagement_panel.refresh()

    assert window.active_engagement == "R26-0004"
    assert window.engagement_panel.ref_label.text() == "R26-0004"
