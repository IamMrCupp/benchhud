"""Tests for the Inventory panel (in-memory source, offscreen).

The behaviours that matter: the list shows what the server says (status
displayed, never re-derived), consume goes through the seam with the selected
job's ticket ref, and a refused write is shown — never retried, never silent.
"""

from __future__ import annotations

import pytest

from benchhud.config import Config
from benchhud.inventory import (
    HttpInventorySource,
    InMemoryInventorySource,
    InventoryWriteError,
    NullInventorySource,
    Part,
)
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.inventory import InventoryPanel

_PARTS = [
    Part(
        id=7,
        description="Dual MOSFET",
        mpn="IRF7910",
        status="low",
        quantity_on_hand=2,
        min_quantity=4,
        location="A3",
    ),
    Part(id=9, description="PETG teal", status="out", quantity_on_hand=0, min_quantity=1),
    Part(id=11, description="1000 uF cap", status="ok", quantity_on_hand=12, min_quantity=2),
]


@pytest.fixture
def source() -> InMemoryInventorySource:
    return InMemoryInventorySource(list(_PARTS))


def _panel(qtbot, source, **kw) -> InventoryPanel:
    panel = InventoryPanel(lambda: source, **kw)
    qtbot.addWidget(panel)
    return panel


def test_metadata(qtbot, source) -> None:
    panel = _panel(qtbot, source)

    assert panel.panel_id == "inventory"
    assert panel.objectName() == "panel__inventory"


def test_lists_low_stock_by_default(qtbot, source) -> None:
    panel = _panel(qtbot, source)

    texts = [panel.part_list.item(i).text() for i in range(panel.part_list.count())]

    assert len(texts) == 2  # the "ok" part is not on the low list
    assert "IRF7910" in texts[0]
    assert "2 on hand (min 4)" in texts[0]
    assert "A3" in texts[0]


def test_search_shows_matches_instead_of_low_stock(qtbot, source) -> None:
    panel = _panel(qtbot, source)
    panel.search_edit.setText("cap")

    panel.refresh()

    assert panel.part_list.count() == 1
    assert "1000 uF cap" in panel.part_list.item(0).text()


def test_consume_records_against_the_job_ref_and_updates_the_row(qtbot, source) -> None:
    refs: list = []

    class _Spy(InMemoryInventorySource):
        def consume(self, part_id, quantity, ticket_ref=None):
            refs.append(ticket_ref)
            return super().consume(part_id, quantity, ticket_ref)

    spy = _Spy(list(_PARTS))
    panel = _panel(qtbot, spy, job_ref=lambda: "26-0001")
    panel.part_list.setCurrentRow(0)
    panel.qty_spin.setValue(2)

    panel.consume_selected()

    assert refs == ["26-0001"]
    assert "Used 2 \u00d7 IRF7910 on 26-0001" in panel.message_label.text()
    assert "0 left" in panel.message_label.text()
    assert spy.get_part(7).quantity_on_hand == 0


def test_consume_failure_is_shown_and_not_retried(qtbot) -> None:
    calls: list = []

    class _Refusing(NullInventorySource):
        def list_low_stock(self):
            return [_PARTS[0]]

        def consume(self, part_id, quantity, ticket_ref=None):
            calls.append(part_id)
            raise InventoryWriteError("insufficient stock: 2 on hand, 5 requested")

    panel = _panel(qtbot, _Refusing())
    panel.part_list.setCurrentRow(0)
    panel.qty_spin.setValue(5)

    panel.consume_selected()

    assert calls == [7]  # exactly one attempt — no retry
    assert "Not recorded" in panel.message_label.text()
    assert "insufficient stock" in panel.message_label.text()


def test_consume_success_calls_the_hook(qtbot, source) -> None:
    seen: list = []
    panel = _panel(qtbot, source, on_consumed=lambda part, qty: seen.append((part.id, qty)))
    panel.part_list.setCurrentRow(0)
    panel.qty_spin.setValue(1)

    panel.consume_selected()

    assert seen == [(7, 1)]


def test_use_button_disabled_without_a_selection(qtbot, source) -> None:
    panel = _panel(qtbot, source)

    assert not panel.use_button.isEnabled()
    panel.part_list.setCurrentRow(0)
    assert panel.use_button.isEnabled()


def test_null_source_shows_the_not_configured_hint(qtbot) -> None:
    panel = _panel(qtbot, NullInventorySource())

    assert panel.part_list.count() == 0
    assert "No inventory backend configured" in panel.message_label.text()


def test_healthy_shelf_message_when_configured_but_nothing_low(qtbot) -> None:
    panel = _panel(qtbot, InMemoryInventorySource([_PARTS[2]]))

    assert "shelf is healthy" in panel.message_label.text()


# --- MainWindow wiring -------------------------------------------------------


def test_window_uses_null_inventory_source_without_config(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert isinstance(window._inventory_source, NullInventorySource)
    assert window.inventory_panel.panel_id == "inventory"


def test_window_builds_http_inventory_source_when_configured(qtbot) -> None:
    cfg = Config()
    cfg.inventory.base_url = "http://parts:8000"
    cfg.inventory.token = "tok"
    window = MainWindow(config=cfg)
    qtbot.addWidget(window)

    source = window._inventory_source
    assert isinstance(source, HttpInventorySource)
    assert source.base_url == "http://parts:8000"


def test_consume_drops_a_note_on_the_selected_job(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window._inventory_source = InMemoryInventorySource(list(_PARTS))
    window.inventory_panel.refresh()
    job = window._job_store.create_job("MacBook A1706")
    window.jobs_panel.refresh_jobs()
    window.jobs_panel.select_job(job.id)
    window.inventory_panel.part_list.setCurrentRow(0)
    window.inventory_panel.qty_spin.setValue(2)

    window.inventory_panel.consume_selected()

    notes = window._job_store.list_notes(job.id)
    assert any("Used 2 \u00d7 IRF7910" in n.text for n in notes)


def test_selected_job_ref_reads_the_linked_ticket(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.selected_job_ref() is None
