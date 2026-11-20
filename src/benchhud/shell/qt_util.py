"""Small Qt helpers shared across the shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QMainWindow, QPushButton, QTabBar

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


#: A heading's letter spacing, as a percentage of the font's own.
_HEADING_SPACING = 106.0
#: A heading's size against the text around it.
_HEADING_SCALE = 0.88


def heading_font(base: QFont) -> QFont:
    """``base`` as a panel heading: small caps, a little letter spacing."""
    font = QFont(base)
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, _HEADING_SPACING)
    if base.pointSizeF() > 0:
        font.setPointSizeF(base.pointSizeF() * _HEADING_SCALE)
    return font


def plain_font(base: QFont) -> QFont:
    """``base`` with a heading's caps, spacing and size explicitly undone.

    Explicitly, because a font is inherited: the widgets inside a titled one
    take its heading font unless theirs says otherwise.
    """
    font = QFont(base)
    font.setCapitalization(QFont.Capitalization.MixedCase)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0)
    if base.pointSizeF() > 0:
        font.setPointSizeF(base.pointSizeF())
    return font


def section_label(text: str) -> QLabel:
    """A heading for a run of controls inside a panel; the theme colours it."""
    label = QLabel(text)
    label.setObjectName("sectionLabel")
    label.setFont(heading_font(label.font()))
    return label


def mark_danger(button: QPushButton) -> QPushButton:
    """Mark ``button`` as one that destroys something; the theme draws it so."""
    button.setProperty("danger", True)
    return button


def hint(label: QLabel) -> QLabel:
    """Mark ``label`` as secondary text, which the theme draws muted."""
    label.setObjectName("hint")
    return label


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
