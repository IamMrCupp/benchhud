"""Starting up when the config file can't be read.

A typo in a hand-edited ``config.toml`` used to be an uncaught exception
before the window existed: nothing on screen, nothing a person at the bench
would see. Now the app comes up anyway, and says so.

Two rules, because the file is somebody's settings and tokens:

- **Run on defaults.** A window with nothing configured is a better place to
  read an error message than no window.
- **Save nothing over the file.** benchhud saves its config when it closes and
  when Preferences is OK'd. On defaults, either would replace the file with
  them — so for this run saving is switched off, and the message says that.
  The file stays exactly as it was until it's fixed.

The Qt part is one function and the rest is plain Python, so the wording and
the saving rule test without a window.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from benchhud.config import Config, ConfigError

log = logging.getLogger(__name__)

HEADLINE = "benchhud couldn't read its config, so it's running on default settings."


def load_or_defaults(load: Callable[[], Config]) -> tuple[Config, ConfigError | None]:
    """The config to run on, and what went wrong reading it (``None`` if nothing).

    Only a file that's there and unusable is a problem. A file that isn't
    there is a first run, and gets defaults with no fuss.
    """
    try:
        return load(), None
    except ConfigError as problem:
        return Config(), problem


def explain(problem: ConfigError) -> str:
    """What to tell the operator, under :data:`HEADLINE`."""
    return (
        f"{problem.path}\n"
        f"{problem.reason}\n\n"
        "Nothing will be saved this session, so the file stays exactly as it is."
        " Fix it and start benchhud again."
    )


def saver(save: Callable[[Config], Any], problem: ConfigError | None) -> Callable[[Config], None]:
    """The "persist this config" callback for a run that started with ``problem``.

    With a problem it saves nothing, and logs why each time it's asked. With
    none it saves — and a save that fails is logged, not raised, because the
    callers are a window closing and a dialog's OK button.
    """

    def persist(config: Config) -> None:
        if problem is not None:
            log.warning(
                "not saving the config: %s couldn't be read when benchhud started,"
                " so it's left as it is",
                problem.path,
            )
            return
        try:
            save(config)
        except (OSError, ConfigError) as exc:
            log.error("couldn't save the config: %s", exc)

    return persist


def show(parent: Any, problem: ConfigError) -> Any:
    """Put the message up over ``parent``, without holding the app up for it.

    Returns the box. It isn't a blocking dialog: cameras, output and the rest
    start behind it as on any other run.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(
        QMessageBox.Icon.Warning, "benchhud", HEADLINE, QMessageBox.StandardButton.Ok, parent
    )
    box.setInformativeText(explain(problem))
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    box.open()
    return box
