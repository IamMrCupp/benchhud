"""Tests for the look: the stylesheet, light and dark, and View > Appearance."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from benchhud.shell import theme as theme_module
from benchhud.shell.theme import DARK_QSS, apply_theme, current_look, stylesheet
from benchhud.themes import DARK, LIGHT, resolve


@pytest.fixture(autouse=True)
def _reset_look(qtbot):
    """apply_theme changes the whole application: put it back for the next test."""
    yield
    app = QApplication.instance()
    app.setStyleSheet("")
    app.styleHints().unsetColorScheme()
    theme_module._current = resolve("dark", system_dark=True, themes=[])


def _window(qtbot, tmp_path, **kwargs):
    from benchhud.jobs.store import JobStore
    from benchhud.shell.main_window import MainWindow

    window = MainWindow(job_store=JobStore(":memory:"), themes_folder=tmp_path, **kwargs)
    qtbot.addWidget(window)
    return window


def test_apply_theme_sets_stylesheet(qtbot) -> None:
    app = QApplication.instance()
    assert app is not None
    apply_theme(app)
    assert app.styleSheet() == stylesheet()
    assert app.styleSheet().startswith(DARK_QSS)
    assert "background-color" in app.styleSheet()


def test_checkboxes_have_a_visible_box_and_mark(qtbot) -> None:
    """The native box was painted over by the window colour (#286)."""
    from pathlib import Path

    qss = stylesheet()
    assert "QCheckBox::indicator" in qss and "{check}" not in qss
    check = qss.split('image: url("', 1)[1].split('")', 1)[0]
    assert Path(check).is_file()


def test_group_titles_are_small_caps_and_their_contents_are_not(qtbot) -> None:
    """Burnwright's panel headings (#440): a font is inherited, so the body is reset."""
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QGroupBox

    from benchhud.jobs.store import JobStore
    from benchhud.shell.main_window import MainWindow

    window = MainWindow(job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    upper, mixed = QFont.Capitalization.AllUppercase, QFont.Capitalization.MixedCase

    group = window.power_panel.findChild(QGroupBox)
    assert group.font().capitalization() == upper
    assert all(
        child.font().capitalization() == mixed
        for child in group.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
    )
    assert window.controls_panel.mode_combo.font().capitalization() == mixed


def test_erase_and_write_are_marked_as_destructive(qtbot) -> None:
    from benchhud.jobs.store import JobStore
    from benchhud.shell.main_window import MainWindow

    window = MainWindow(job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    for panel in (window.flash_panel, window.minipro_panel):
        assert panel.erase_button.property("danger") is True
        assert panel.write_button.property("danger") is True
        assert not panel.read_button.property("danger")
    assert 'QPushButton[danger="true"]' in stylesheet()


# --- light, dark and custom ---------------------------------------------------


def _hexes(qss: str) -> set[str]:
    return set(re.findall(r"#[0-9a-f]{6}\b", qss))


def test_every_placeholder_is_filled_in_every_look() -> None:
    for choice in ("light", "dark"):
        qss = stylesheet(resolve(choice, system_dark=True, themes=[]))
        assert not re.search(r"@[a-z]", qss), choice


def test_the_light_look_uses_none_of_the_dark_colours() -> None:
    qss = stylesheet(resolve("light", system_dark=True, themes=[]))
    assert LIGHT["window"] in qss and LIGHT["panel"] in qss
    assert not _hexes(qss) & (set(DARK.values()) - set(LIGHT.values()))


def test_the_check_mark_is_white_on_the_light_looks_deep_accent() -> None:
    light = stylesheet(resolve("light", system_dark=True, themes=[]))
    mark = light.split('image: url("', 1)[1].split('")', 1)[0]
    assert mark.endswith("check-light.svg") and Path(mark).is_file()
    assert "#ffffff" in Path(mark).read_text()


def test_apply_theme_tells_the_native_parts_too(qtbot) -> None:
    from PySide6.QtCore import Qt

    from benchhud.shell.theme import tell_native

    app = QApplication.instance()
    look = apply_theme(app, "light")
    assert look.base == "light" and current_look() is look
    assert LIGHT["window"] in app.styleSheet()

    class Hints:
        def __init__(self) -> None:
            self.asked: list = []

        def setColorScheme(self, scheme) -> None:
            self.asked.append(scheme)

    hints = Hints()
    tell_native(hints, "light", look)
    tell_native(hints, "dark", resolve("dark", system_dark=False, themes=[]))
    tell_native(hints, "system", look)  # following it: nothing to say
    assert hints.asked == [Qt.ColorScheme.Light, Qt.ColorScheme.Dark]


def test_a_custom_themes_colours_reach_the_stylesheet(qtbot, tmp_path) -> None:
    from benchhud.themes import load_themes

    (tmp_path / "amber.json").write_text(
        json.dumps({"name": "Amber", "base": "dark", "colors": {"bg": "#120d02"}})
    )
    app = QApplication.instance()
    apply_theme(app, "theme:amber.json", load_themes(tmp_path)[0])

    assert "#120d02" in app.styleSheet()


def test_panels_colour_rows_from_the_look_not_their_own_hex() -> None:
    """The colours live in the theme, so they change with it."""
    root = Path(__file__).resolve().parents[1] / "src" / "benchhud" / "shell" / "panels"
    for name in ("controls.py", "inventory.py", "readings.py", "terminal.py"):
        text = (root / name).read_text()
        assert not re.search(r"(color: *#|QColor\(\"#)", text), name


# --- View > Appearance --------------------------------------------------------


def test_the_menu_offers_system_light_and_dark(qtbot, tmp_path) -> None:
    window = _window(qtbot, tmp_path)

    assert list(window.appearance_actions) == ["system", "light", "dark"]
    assert window.appearance_actions["dark"].isChecked()  # the default


def test_choosing_a_look_applies_it_and_saves_it(qtbot, tmp_path) -> None:
    saved = []
    window = _window(qtbot, tmp_path, on_persist=saved.append)

    window.appearance_actions["light"].trigger()

    assert window._config.shell.appearance == "light"
    assert saved and saved[-1].shell.appearance == "light"
    assert LIGHT["window"] in QApplication.instance().styleSheet()


def test_a_theme_dropped_in_the_folder_shows_up_next_time_the_menu_opens(qtbot, tmp_path) -> None:
    window = _window(qtbot, tmp_path)
    (tmp_path / "amber.json").write_text(json.dumps({"name": "Amber", "base": "dark"}))
    (tmp_path / "broken.json").write_text("{")

    window.fill_appearance_menu()

    assert window.appearance_actions["theme:amber.json"].text() == "Amber"
    labels = [a.text() for a in window.appearance_menu.actions()]
    broken = [a for a in window.appearance_menu.actions() if a.text().startswith("broken.json")]
    assert broken and not broken[0].isEnabled(), labels


def test_following_the_system_reapplies_when_it_changes(qtbot, tmp_path) -> None:
    window = _window(qtbot, tmp_path)
    window.set_appearance("system")
    QApplication.instance().setStyleSheet("")

    window._system_scheme_changed()

    assert QApplication.instance().styleSheet()


def test_a_fixed_look_ignores_the_system_changing(qtbot, tmp_path) -> None:
    window = _window(qtbot, tmp_path)
    window.set_appearance("dark")
    QApplication.instance().setStyleSheet("")

    window._system_scheme_changed()

    assert QApplication.instance().styleSheet() == ""


# --- dropdown and spin box arrows ----------------------------------------------


def _images(qss: str, selector: str) -> list[Path]:
    """The image files the rules for ``selector`` point at."""
    found = []
    for rule in re.findall(re.escape(selector) + r"[^{]*\{[^}]*\}", qss):
        found += [Path(p) for p in re.findall(r'image: url\("([^"]+)"\)', rule)]
    return found


@pytest.mark.parametrize("choice", ["light", "dark"])
def test_arrows_are_drawn_flat_in_the_looks_colours(choice) -> None:
    """Qt's own buttons had a bevelled dark edge that stood out on the light look."""
    look = resolve(choice, system_dark=True, themes=[])
    qss = stylesheet(look)

    assert re.search(r"QComboBox::drop-down \{[^}]*border: none", qss)
    muted = look.qss()["muted"]
    for selector in ("QComboBox::down-arrow", "QAbstractSpinBox::up-arrow"):
        arrows = _images(qss, selector)
        assert arrows and all(a.is_file() for a in arrows), selector
        assert muted in arrows[0].read_text()


def test_a_disabled_arrow_is_fainter(qtbot) -> None:
    look = resolve("light", system_dark=True, themes=[])
    disabled = _images(stylesheet(look), "QComboBox::down-arrow:disabled")
    assert disabled and look.qss()["faint"] in disabled[0].read_text()


def test_a_custom_themes_arrows_follow_its_muted_colour(tmp_path) -> None:
    from benchhud.themes import load_themes

    (tmp_path / "amber.json").write_text(
        json.dumps({"base": "dark", "colors": {"muted": "#b07a10"}})
    )
    look = resolve("theme:amber.json", system_dark=True, themes=load_themes(tmp_path)[0])
    arrows = _images(stylesheet(look), "QComboBox::down-arrow")
    assert "#b07a10" in arrows[0].read_text()
