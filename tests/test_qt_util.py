"""Tests for shared Qt helpers."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QComboBox, QLabel

from benchhud.shell.qt_util import TONES, card, fit_combo_popup, notice, set_tone
from benchhud.shell.theme import stylesheet


def test_popup_widens_to_the_longest_item(qtbot) -> None:
    combo = QComboBox()
    qtbot.addWidget(combo)
    combo.addItems(["HOT", "INFERNO", "A-VERY-LONG-COLORMAP-NAME"])
    combo.setFixedWidth(60)  # narrow, as in a cramped panel

    fit_combo_popup(combo)

    longest = combo.fontMetrics().horizontalAdvance("A-VERY-LONG-COLORMAP-NAME")
    assert combo.view().minimumWidth() >= longest  # no truncation
    assert combo.view().minimumWidth() > combo.width()


def test_empty_combo_is_safe(qtbot) -> None:
    combo = QComboBox()
    qtbot.addWidget(combo)
    fit_combo_popup(combo)  # must not raise on an empty combo


def test_controls_panel_combos_are_fitted(qtbot) -> None:
    from benchhud.shell.panels.controls import ControlsPanel

    panel = ControlsPanel()
    qtbot.addWidget(panel)
    metrics = panel.colormap_combo.fontMetrics()
    widest = max(
        metrics.horizontalAdvance(panel.colormap_combo.itemText(i))
        for i in range(panel.colormap_combo.count())
    )
    assert panel.colormap_combo.view().minimumWidth() >= widest


def test_a_tone_sets_the_property_the_theme_colours(qtbot) -> None:
    label = QLabel("Couldn't reach the meter")
    qtbot.addWidget(label)
    set_tone(label, "error")
    assert label.property("tone") == "error"
    set_tone(label, None)
    assert label.property("tone") is None


def test_an_unknown_tone_is_refused(qtbot) -> None:
    label = QLabel()
    qtbot.addWidget(label)
    with pytest.raises(ValueError, match="tone"):
        set_tone(label, "purple")


def test_every_tone_has_a_colour_in_the_theme() -> None:
    sheet = stylesheet()
    for tone in TONES:
        assert f'QLabel[tone="{tone}"]' in sheet


def test_a_notice_wraps_and_takes_its_tone(qtbot) -> None:
    label = notice(QLabel("No reference for this board."), "warning")
    qtbot.addWidget(label)
    assert label.objectName() == "notice"
    assert label.wordWrap()
    assert label.property("tone") == "warning"


def test_a_card_titles_its_section_in_capitals(qtbot) -> None:
    box, form = card("Alignment")
    qtbot.addWidget(box)
    assert box.title() == "ALIGNMENT"
    assert box.layout() is form
