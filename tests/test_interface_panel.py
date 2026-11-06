"""The connected-interfaces monitor: USB enumeration, cross-map, binding."""

from __future__ import annotations

from dataclasses import dataclass

from benchhud.assets import Asset, InMemoryAssetSource, NullAssetSource
from benchhud.devices.discovery import UsbInterface, list_usb_interfaces
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.interfaces import InterfacePanel, auto_match


@dataclass
class _FakePort:
    device: str
    description: str = ""
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    manufacturer: str | None = None
    product: str | None = None


_JLINK_A = Asset(
    id=1, name="J-Link A", asset_type="debugger", serial="AAA111", assigned_ref="R26-1"
)
_JLINK_B = Asset(
    id=2, name="J-Link B", asset_type="debugger", serial="BBB222", assigned_ref="R26-1"
)
_HACKRF = Asset(id=3, name="HackRF", asset_type="sdr", serial="", assigned_ref="R26-1")

_PORT_A = UsbInterface("/dev/ttyACM0", "J-Link", vid=0x1366, pid=0x0105, serial_number="AAA111")
_PORT_UNK = UsbInterface("/dev/ttyUSB0", "CP2102 UART", vid=0x10C4, pid=0xEA60, serial_number=None)


# --- discovery: list_usb_interfaces --------------------------------------


def test_list_usb_interfaces_keeps_the_usb_identity() -> None:
    ports = [_FakePort("/dev/ttyACM0", "J-Link", 0x1366, 0x0105, "000998")]
    [iface] = list_usb_interfaces(comports=lambda: ports, stable_fn=lambda _p: None)
    assert iface.device == "/dev/ttyACM0"
    assert iface.vid_pid == "1366:0105"
    assert iface.serial_number == "000998"


def test_usb_interface_vid_pid_is_none_without_both() -> None:
    assert UsbInterface("/dev/ttyS0", "builtin").vid_pid is None


# --- cross-map: auto_match (serial only) ---------------------------------


def test_auto_match_binds_on_serial() -> None:
    assert auto_match(_PORT_A, [_JLINK_A, _JLINK_B]) == _JLINK_A


def test_auto_match_ignores_vid_pid_and_empty_serial() -> None:
    # Same VID:PID, different serial → the port with no serial matches nothing,
    # so two identical probes are never silently bound to the wrong asset.
    assert auto_match(_PORT_UNK, [_JLINK_A, _JLINK_B]) is None
    port_no_serial = UsbInterface("/dev/ttyACM9", "J-Link", 0x1366, 0x0105, serial_number="")
    assert auto_match(port_no_serial, [_JLINK_A]) is None


# --- panel ---------------------------------------------------------------


def _panel(qtbot, assets, ports, ref="R26-1") -> InterfacePanel:
    panel = InterfacePanel(
        lambda: InMemoryAssetSource(assets=assets) if assets is not None else NullAssetSource(),
        active_ref=lambda: ref,
        interfaces=lambda: ports,
    )
    qtbot.addWidget(panel)
    return panel


def _asset_cell(panel: InterfacePanel, row: int) -> str:
    return panel.tree.topLevelItem(row).text(4)


def test_panel_auto_maps_a_matching_serial(qtbot) -> None:
    panel = _panel(qtbot, [_JLINK_A, _JLINK_B], [_PORT_A])
    assert "J-Link A (auto)" in _asset_cell(panel, 0)


def test_panel_unmatched_port_shows_dash(qtbot) -> None:
    panel = _panel(qtbot, [_JLINK_A], [_PORT_UNK])
    assert _asset_cell(panel, 0) == "—"


def test_manual_binding_overrides_and_persists_in_session(qtbot) -> None:
    panel = _panel(qtbot, [_JLINK_A, _JLINK_B, _HACKRF], [_PORT_UNK])
    # Select the unmatched port, choose the HackRF in the combo, bind.
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    hackrf_index = next(
        i
        for i in range(panel.asset_combo.count())
        if panel.asset_combo.itemData(i).id == _HACKRF.id
    )
    panel.asset_combo.setCurrentIndex(hackrf_index)
    panel.bind_selected()
    assert "HackRF (manual)" in _asset_cell(panel, 0)
    assert panel._bindings["/dev/ttyUSB0"] == _HACKRF.id


def test_manual_binding_beats_a_serial_auto_match(qtbot) -> None:
    panel = _panel(qtbot, [_JLINK_A, _JLINK_B], [_PORT_A])
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    b_index = next(
        i
        for i in range(panel.asset_combo.count())
        if panel.asset_combo.itemData(i).id == _JLINK_B.id
    )
    panel.asset_combo.setCurrentIndex(b_index)
    panel.bind_selected()
    assert "J-Link B (manual)" in _asset_cell(panel, 0)


def test_unbind_falls_back_to_auto(qtbot) -> None:
    panel = _panel(qtbot, [_JLINK_A, _JLINK_B], [_PORT_A])
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    b_index = next(
        i
        for i in range(panel.asset_combo.count())
        if panel.asset_combo.itemData(i).id == _JLINK_B.id
    )
    panel.asset_combo.setCurrentIndex(b_index)
    panel.bind_selected()
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    panel.unbind_selected()
    assert "J-Link A (auto)" in _asset_cell(panel, 0)


def test_no_engagement_prompts_to_import(qtbot) -> None:
    panel = _panel(qtbot, [_JLINK_A], [_PORT_A], ref=None)
    assert "Import an engagement" in panel.message_label.text()
    assert _asset_cell(panel, 0) == "—"  # no assets to map without an engagement


def test_no_interfaces_says_so(qtbot) -> None:
    panel = _panel(qtbot, [_JLINK_A], [])
    assert "No serial/USB interfaces" in panel.message_label.text()


# --- window wiring -------------------------------------------------------


def test_interface_panel_is_not_sensitive() -> None:
    assert InterfacePanel.sensitive is False


def test_window_builds_the_interface_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window.interface_panel, InterfacePanel)
