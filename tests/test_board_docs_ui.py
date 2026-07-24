"""Tests for opening board views, the search dialog, and the to-source worklist."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchhud.boards.library import BoardLibrary
from benchhud.boards.opener import find_board_viewer, open_board
from benchhud.boards.worklist import boards_to_source
from benchhud.config import Config, ToolConfig
from benchhud.jobs.store import JobStore
from benchhud.launcher.tool_launcher import ToolLauncher
from benchhud.shell.board_dialogs import BoardSearchDialog, BoardsToSourceDialog
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.jobs import JobsPanel, preferred_board_file
from benchhud.tickets.source import InMemoryTicketSource, Ticket

_OBV = ToolConfig(name="OpenBoardView", command="openboardview")
_FLEX = ToolConfig(name="FlexBV", command="flexbv")


@pytest.fixture
def library(tmp_path) -> BoardLibrary:
    (tmp_path / "820-00239.obv").write_text("x")
    (tmp_path / "820-00239.brd").write_text("x")
    (tmp_path / "051-01234.bdv").write_text("x")
    return BoardLibrary(root=tmp_path)


# --- viewer discovery ----------------------------------------------------


def test_finds_the_configured_obv_tool() -> None:
    assert find_board_viewer([_FLEX, _OBV]) is _OBV


def test_matches_on_command_and_ignores_spacing_or_case() -> None:
    assert find_board_viewer([ToolConfig(name="Open Board View", command="/opt/obv")]) is not None


def test_flexbv_is_not_treated_as_obv() -> None:
    """The handoff specifies OBV, not FlexBV."""
    assert find_board_viewer([_FLEX]) is None


# --- open_board ----------------------------------------------------------


def test_open_board_launches_obv_with_the_file(tmp_path) -> None:
    board = tmp_path / "820-00239.obv"
    board.write_text("x")
    spawned: list[list[str]] = []
    launcher = ToolLauncher(spawn=spawned.append)

    assert open_board(board, [_OBV], launcher=launcher) is True
    assert spawned == [["openboardview", str(board)]]


def test_open_board_falls_back_to_the_os_handler(tmp_path) -> None:
    board = tmp_path / "820-00239.obv"
    board.write_text("x")
    opened: list[Path] = []

    assert open_board(board, [], os_open=opened.append) is True
    assert opened == [board]


def test_open_board_missing_file_is_false_not_fatal(tmp_path) -> None:
    spawned: list[list[str]] = []
    result = open_board(tmp_path / "nope.obv", [_OBV], launcher=ToolLauncher(spawn=spawned.append))
    assert result is False
    assert spawned == []


def test_open_board_survives_a_viewer_that_wont_start(tmp_path) -> None:
    board = tmp_path / "b.obv"
    board.write_text("x")

    def _boom(_argv):
        raise OSError("no such binary")

    assert open_board(board, [_OBV], launcher=ToolLauncher(spawn=_boom)) is False


def test_launcher_appends_args() -> None:
    spawned: list[list[str]] = []
    ToolLauncher(spawn=spawned.append).launch(ToolConfig(name="t", command="run -x"), ["file"])
    assert spawned == [["run", "-x", "file"]]


def test_preferred_board_file_favours_obv(library) -> None:
    assert preferred_board_file(library.resolve("820-00239")).format == "obv"


# --- worklist ------------------------------------------------------------


def _ticket(ref: str, board_id: str | None, device: str = "CDJ-3000") -> Ticket:
    return Ticket(ref=ref, device=device, board_id=board_id)


def test_worklist_lists_only_boards_not_in_the_library(library) -> None:
    needs = boards_to_source([_ticket("26-1", "820-00239"), _ticket("26-2", "111-99999")], library)
    assert [n.board_id for n in needs] == ["111-99999"]


def test_worklist_groups_tickets_needing_the_same_board(library) -> None:
    needs = boards_to_source(
        [_ticket("26-1", "111-99999"), _ticket("26-2", "111-99999", device="iPhone 12")], library
    )
    assert len(needs) == 1
    assert needs[0].refs == ("26-1", "26-2")
    assert needs[0].devices == ("CDJ-3000", "iPhone 12")


def test_worklist_skips_tickets_without_a_board_id(library) -> None:
    assert boards_to_source([_ticket("26-1", None), _ticket("26-2", "  ")], library) == []


def test_worklist_summary_reads_naturally(library) -> None:
    needs = boards_to_source([_ticket("26-1", "111-99999")], library)
    assert needs[0].summary == "111-99999 — 1 ticket (CDJ-3000)"


# --- search dialog -------------------------------------------------------


def test_search_dialog_lists_and_filters(qtbot, library) -> None:
    dialog = BoardSearchDialog(library)
    qtbot.addWidget(dialog)
    assert dialog.tree.topLevelItemCount() == 3  # blank query lists everything
    dialog.query_edit.setText("051")
    assert dialog.tree.topLevelItemCount() == 1


def test_search_dialog_opens_the_selected_board(qtbot, library) -> None:
    opened: list[Path] = []
    dialog = BoardSearchDialog(library, open_board=lambda p: bool(opened.append(p)) or True)
    qtbot.addWidget(dialog)
    dialog.query_edit.setText("051")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    assert dialog.open_selected() is True
    assert opened[0].name == "051-01234.bdv"


def test_search_dialog_without_a_library_says_so(qtbot) -> None:
    dialog = BoardSearchDialog(BoardLibrary.from_config(""))
    qtbot.addWidget(dialog)
    assert dialog.tree.topLevelItemCount() == 0
    assert "No board library configured" in dialog.status_label.text()


def test_to_source_dialog_lists_needs(qtbot, library) -> None:
    needs = boards_to_source([_ticket("26-1", "111-99999")], library)
    dialog = BoardsToSourceDialog(needs)
    qtbot.addWidget(dialog)
    assert dialog.tree.topLevelItemCount() == 1
    assert dialog.tree.topLevelItem(0).text(0) == "111-99999"


# --- Jobs panel board row ------------------------------------------------


def _panel(qtbot, library: BoardLibrary, opened: list[Path]) -> JobsPanel:
    panel = JobsPanel(
        JobStore(":memory:"),
        board_library=lambda: library,
        open_board=lambda p: bool(opened.append(p)) or True,
    )
    qtbot.addWidget(panel)
    return panel


def test_board_row_hidden_for_a_job_without_a_board(qtbot, library) -> None:
    panel = _panel(qtbot, library, [])
    panel._store.create_job("walk-in")
    panel.refresh_jobs()
    assert not panel._board_row.isVisibleTo(panel)


def test_board_row_reports_a_board_in_the_library(qtbot, library) -> None:
    panel = _panel(qtbot, library, [])
    job = panel._store.create_job("CDJ-3000", board_id="820-00239")
    panel.refresh_jobs()
    panel.select_job(job.id)
    assert "in library" in panel.board_label.text()
    assert panel.open_board_button.isEnabled()


def test_board_row_reports_a_board_we_dont_have(qtbot, library) -> None:
    panel = _panel(qtbot, library, [])
    job = panel._store.create_job("CDJ-3000", board_id="111-99999")
    panel.refresh_jobs()
    panel.select_job(job.id)
    assert "not in library" in panel.board_label.text()
    assert not panel.open_board_button.isEnabled()


def test_open_board_for_selected_uses_the_obv_file(qtbot, library) -> None:
    opened: list[Path] = []
    panel = _panel(qtbot, library, opened)
    job = panel._store.create_job("CDJ-3000", board_id="820-00239")
    panel.refresh_jobs()
    panel.select_job(job.id)
    assert panel.open_board_for_selected() is True
    assert opened[0].suffix == ".obv"


# --- MainWindow wiring ---------------------------------------------------


def test_window_builds_the_library_from_config(qtbot, tmp_path) -> None:
    cfg = Config()
    cfg.boards.library_dir = str(tmp_path)
    window = MainWindow(cfg)
    qtbot.addWidget(window)
    assert window._board_library.configured


def test_reconfigure_boards_picks_up_config_change(qtbot, tmp_path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert not window._board_library.configured
    window._config.boards.library_dir = str(tmp_path)
    window.reconfigure_boards()
    assert window._board_library.configured


def test_boards_to_source_dialog_from_the_live_queue(qtbot, tmp_path) -> None:
    cfg = Config()
    cfg.boards.library_dir = str(tmp_path)  # empty library → everything is missing
    window = MainWindow(cfg)
    qtbot.addWidget(window)
    window._ticket_source = InMemoryTicketSource([_ticket("26-1", "111-99999")])
    dialog = window.open_boards_to_source()
    qtbot.addWidget(dialog)
    assert dialog.tree.topLevelItemCount() == 1
    dialog.close()
