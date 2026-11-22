"""Sending a chip to the stream card — the words for it, in one place.

The Chip panel is stream output: a viewer can read it. Two operator screens
can put a chip there — Find chip and the inventory row — and both have to say
the same thing about what that means, so the button's name, its tooltip and
the line that confirms it live here.

Only a part number ever crosses over. The card looks the chip up for itself
and decides what of it to show; nothing from the screen that sent it — a note,
a source's name, a value that lost, anything about a job — goes with it.

No Qt, and nothing here comes from a chip record except the part number in
:func:`sent_line`, which the caller draws as plain text.
"""

from __future__ import annotations

#: The action's name. "Stream" is in it on purpose: this is the operator
#: choosing to put something on air.
SHOW_ON_CARD = "Show on stream card"

#: The action's tooltip — this module's own words, so it's safe as a tooltip.
SHOW_ON_CARD_TIP = (
    "Puts this chip on the Chip panel, which is stream output: viewers see its part"
    " number, maker, package, functions, marking codes and description. It replaces"
    " the chip that's there."
)

#: Why the action is off when there's no chip to send.
NOTHING_TO_SHOW = "Pick a chip first."


def sent_line(mpn: str) -> str:
    """What to say once a chip has been sent to the card."""
    return f"{mpn} is on the Chip panel now — that card is stream output."
