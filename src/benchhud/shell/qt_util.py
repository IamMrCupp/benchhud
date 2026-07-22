"""Small Qt helpers shared across the shell."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox

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
