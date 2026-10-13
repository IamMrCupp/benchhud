"""Small Qt helpers shared across the shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFormLayout, QMainWindow, QTabBar

# Room for the dropdown indicator, item padding and a scrollbar.
_POPUP_CHROME_PX = 44


def fit_combo_popup(combo: QComboBox) -> None:
    """Widen a combo's popup to its widest item.

    Qt sizes the popup to the combo itself, so in a narrow panel the item text
    gets truncated ("INFERNO" -> "INFERN"). Call after populating the combo.
    """
    metrics = combo.fontMetrics()
    widest = max(
        (metrics.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())),
        default=0,
    )
    combo.view().setMinimumWidth(widest + _POPUP_CHROME_PX)


def grow_fields(form: QFormLayout) -> None:
    """Let a form's fields fill their column.

    macOS's default keeps every field at its size hint, so in a wide panel a
    line edit sits at 100 px beside empty space.
    """
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)


def unelide_tabs(window: QMainWindow) -> None:
    """Show dock tabs' titles whole, scrolling the tab row when it's too narrow.

    Qt elides tabbed docks' titles to fit ("Invent…", "Termi…"), which in a
    narrow dock leaves every tab unreadable. Qt creates these tab bars on
    demand, so call this again after the layout changes.
    """
    for bar in window.findChildren(QTabBar):
        if bar.parent() is window:
            bar.setElideMode(Qt.TextElideMode.ElideNone)
            bar.setUsesScrollButtons(True)
            bar.setExpanding(False)
