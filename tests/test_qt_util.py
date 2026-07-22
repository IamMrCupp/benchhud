"""Tests for shared Qt helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox

from benchhud.shell.qt_util import fit_combo_popup


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
