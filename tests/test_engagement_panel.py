"""The engagement panel and its picker — offscreen, no network."""

from __future__ import annotations

from benchhud.recon import Engagement, InMemoryReconSource, NullReconSource
from benchhud.shell.engagement_picker import EngagementPickerDialog
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.engagement import END_QUESTION, EngagementPanel

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


# --- ending an engagement (#490) -----------------------------------------


class _Ending:
    """An Engagement panel wired to end what's active, with the confirm scripted."""

    def __init__(self, qtbot, source, *, ref: str | None, answer: bool) -> None:
        self.active = _Active()
        self.active.ref = ref
        self.asked: list[str] = []
        self.ended = 0
        self.answer = answer
        self.panel = EngagementPanel(
            lambda: source,
            active_ref=lambda: self.active.ref,
            on_end=self._end,
            confirm_end=self._confirm,
        )
        qtbot.addWidget(self.panel)

    def _end(self) -> None:
        self.ended += 1
        self.active.ref = None

    def _confirm(self, ref: str) -> bool:
        self.asked.append(ref)
        return self.answer


def test_there_is_nothing_to_end_with_no_engagement(qtbot) -> None:
    ending = _Ending(qtbot, InMemoryReconSource(engagements=[_E1]), ref=None, answer=True)
    assert ending.panel.end_button.isHidden()
    assert ending.panel.end_engagement() is False
    assert ending.asked == []  # not even asked
    assert ending.ended == 0


def test_an_active_engagement_can_be_ended(qtbot) -> None:
    ending = _Ending(qtbot, InMemoryReconSource(engagements=[_E1]), ref="R26-0004", answer=True)
    assert not ending.panel.end_button.isHidden()
    assert ending.panel.end_engagement() is True
    assert ending.asked == ["R26-0004"]
    assert ending.ended == 1
    # Back to the empty state, and nothing left to end.
    assert ending.panel.ref_label.text() == "—"
    assert "No engagement imported" in ending.panel.message_label.text()
    assert ending.panel.end_button.isHidden()


def test_saying_no_ends_nothing(qtbot) -> None:
    """Ending turns web lookups back on, so a stray click mustn't do it."""
    ending = _Ending(qtbot, InMemoryReconSource(engagements=[_E1]), ref="R26-0004", answer=False)
    assert ending.panel.end_engagement() is False
    assert ending.asked == ["R26-0004"]
    assert ending.ended == 0
    assert ending.panel.ref_label.text() == "R26-0004"
    assert not ending.panel.end_button.isHidden()


def test_the_button_goes_through_the_confirm(qtbot) -> None:
    ending = _Ending(qtbot, InMemoryReconSource(engagements=[_E1]), ref="R26-0004", answer=False)
    ending.panel.end_button.click()
    assert ending.asked == ["R26-0004"]
    assert ending.ended == 0


def test_an_engagement_the_backend_cannot_find_can_still_be_ended(qtbot) -> None:
    """Ending is local. bench-recon being down mustn't leave the lock on for the run."""
    ending = _Ending(qtbot, InMemoryReconSource(engagements=[]), ref="R26-0004", answer=True)
    assert "not found" in ending.panel.message_label.text()
    assert not ending.panel.end_button.isHidden()
    assert ending.panel.end_engagement() is True


def test_and_with_the_recon_backend_taken_out_of_the_config(qtbot) -> None:
    ending = _Ending(qtbot, NullReconSource(), ref="R26-0004", answer=True)
    assert ending.panel.import_button.isHidden()  # nothing to import from
    assert not ending.panel.end_button.isHidden()  # still something to end
    assert ending.panel.end_engagement() is True


def test_a_panel_with_no_way_to_end_shows_no_button(qtbot) -> None:
    active = _Active()
    active.ref = "R26-0004"
    panel = EngagementPanel(
        lambda: InMemoryReconSource(engagements=[_E1]), active_ref=lambda: active.ref
    )
    qtbot.addWidget(panel)
    assert panel.end_button.isHidden()
    assert panel.end_engagement() is False


def test_the_question_names_the_ref_and_defaults_to_no(qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    asked: dict = {}

    def question(parent, title, text, buttons, default):
        asked.update(title=title, text=text, default=default)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    active = _Active()
    active.ref = "R26-0004"
    ended: list[int] = []
    panel = EngagementPanel(
        lambda: InMemoryReconSource(engagements=[_E1]),
        active_ref=lambda: active.ref,
        on_end=lambda: ended.append(1),
    )
    qtbot.addWidget(panel)
    assert panel.end_engagement() is False
    assert ended == []
    assert "R26-0004" in asked["text"]
    assert asked["default"] == QMessageBox.StandardButton.No  # Enter doesn't end it


def test_the_question_says_bench_recon_is_untouched() -> None:
    assert "Nothing changes in bench-recon" in END_QUESTION


# --- the window's side of ending -----------------------------------------


def _window_with_engagement(qtbot) -> MainWindow:
    window = MainWindow()
    qtbot.addWidget(window)
    window._recon_source = InMemoryReconSource(engagements=[_E1])
    window.import_engagement("R26-0004")
    for panel in (window.engagement_panel, window.interface_panel, window.pinout_panel):
        panel.refresh()
    window.capture_panel.refresh()
    return window


def test_window_end_engagement_clears_the_ref_and_says_which(qtbot) -> None:
    window = _window_with_engagement(qtbot)
    assert window.end_engagement() == "R26-0004"
    assert window.active_engagement is None
    assert window.end_engagement() is None  # nothing left to end


def test_every_recon_panel_goes_back_to_its_empty_state(qtbot) -> None:
    """Without anyone pressing Refresh on each."""
    window = _window_with_engagement(qtbot)
    assert window.engagement_panel.ref_label.text() == "R26-0004"
    assert window.pinout_panel.ref_label.text() == "R26-0004"
    assert window.interface_panel.ref_label.text() == "R26-0004"
    assert window.capture_panel.ref_label.text() == "R26-0004"

    window.end_engagement()

    assert window.engagement_panel.ref_label.text() == "—"
    assert window.pinout_panel.ref_label.text() == "— (no engagement)"
    assert window.interface_panel.ref_label.text() == "— (no engagement)"
    assert window.capture_panel.ref_label.text() == "— (no engagement)"
    assert window.engagement_panel.end_button.isHidden()


def test_the_panels_button_ends_it_on_the_window(qtbot) -> None:
    window = _window_with_engagement(qtbot)
    window.engagement_panel._confirm_end = lambda ref: True
    window.engagement_panel.end_button.click()
    assert window.active_engagement is None
    assert window.may_link_out() is True


def test_declining_on_the_window_leaves_it_active(qtbot) -> None:
    window = _window_with_engagement(qtbot)
    window.engagement_panel._confirm_end = lambda ref: False
    window.engagement_panel.end_button.click()
    assert window.active_engagement == "R26-0004"
    assert window.may_link_out() is False


def test_ending_puts_nothing_on_air(qtbot) -> None:
    """The recon panels are off air by their own tag, not by an engagement being active."""
    window = _window_with_engagement(qtbot)
    panels = (
        window.engagement_panel,
        window.interface_panel,
        window.pinout_panel,
        window.capture_panel,
    )
    before = [panel.sensitive for panel in panels]
    window.end_engagement()
    assert [panel.sensitive for panel in panels] == before
    # And the two that carry engagement identity were, and are, off air.
    assert window.engagement_panel.sensitive is True
    assert window.capture_panel.sensitive is True
