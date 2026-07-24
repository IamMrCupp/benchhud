"""Tests for importing an intake ticket onto the bench from the Jobs panel."""

from __future__ import annotations

from benchhud.jobs.store import JobStore
from benchhud.shell.panels.jobs import JobsPanel
from benchhud.shell.ticket_picker import TicketPickerDialog
from benchhud.tickets.source import InMemoryTicketSource, Ticket


def _ticket(ref: str = "26-0001") -> Ticket:
    return Ticket(
        ref=ref,
        device="CDJ-3000",
        symptoms="jog wheel dead",
        customer="DJ in LA",
        status="diagnosing",
        board_id="820-00239",
    )


def _panel(qtbot, tickets: list[Ticket] | None = None) -> JobsPanel:
    source = InMemoryTicketSource(tickets or [])
    panel = JobsPanel(JobStore(":memory:"), ticket_source=lambda: source)
    qtbot.addWidget(panel)
    return panel


# --- picker dialog -------------------------------------------------------


def test_picker_lists_tickets_and_returns_selection(qtbot) -> None:
    dialog = TicketPickerDialog([_ticket("26-0001"), _ticket("26-0002")])
    qtbot.addWidget(dialog)
    assert dialog.tree.topLevelItemCount() == 2
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))
    assert dialog.selected_ticket().ref == "26-0002"


def test_picker_shows_board_id_column(qtbot) -> None:
    dialog = TicketPickerDialog([_ticket()])
    qtbot.addWidget(dialog)
    item = dialog.tree.topLevelItem(0)
    assert item.text(2) == "820-00239"


def test_picker_empty_queue_disables_ok(qtbot) -> None:
    dialog = TicketPickerDialog([])
    qtbot.addWidget(dialog)
    assert dialog.selected_ticket() is None
    assert not dialog._ok_button.isEnabled()


# --- import onto the bench -----------------------------------------------


def test_import_onto_bench_creates_and_selects_job(qtbot) -> None:
    panel = _panel(qtbot)
    job = panel.import_ticket_onto_bench(_ticket("26-0007"))
    assert job.external_ref == "26-0007"
    assert job.device == "CDJ-3000"
    assert panel.selected_job_id() == job.id


def test_import_onto_bench_is_idempotent(qtbot) -> None:
    panel = _panel(qtbot)
    first = panel.import_ticket_onto_bench(_ticket("26-0007"))
    second = panel.import_ticket_onto_bench(_ticket("26-0007"))
    assert first.id == second.id
    assert len(panel._store.list_jobs()) == 1


def test_import_from_ticket_populates_dialog_from_source(qtbot) -> None:
    panel = _panel(qtbot, [_ticket("26-0001"), _ticket("26-0002")])
    dialog = panel.import_from_ticket()
    assert dialog is not None
    qtbot.addWidget(dialog)
    assert dialog.tree.topLevelItemCount() == 2
    dialog.close()


def test_import_selected_imports_the_chosen_ticket(qtbot) -> None:
    panel = _panel(qtbot, [_ticket("26-0009")])
    dialog = panel.import_from_ticket()
    qtbot.addWidget(dialog)
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    panel._import_selected(dialog)
    assert panel._store.find_by_external_ref("26-0009") is not None
    dialog.close()


def test_import_from_ticket_returns_none_without_a_source(qtbot) -> None:
    panel = JobsPanel(JobStore(":memory:"))
    qtbot.addWidget(panel)
    assert panel.import_from_ticket() is None


def test_import_button_visibility_tracks_the_source(qtbot) -> None:
    without = JobsPanel(JobStore(":memory:"))
    qtbot.addWidget(without)
    assert not without.import_button.isVisibleTo(without)

    with_source = _panel(qtbot, [])
    assert with_source.import_button.isVisibleTo(with_source)
