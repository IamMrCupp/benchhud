"""Connected-interfaces monitor — attached probes/adapters, mapped to gear.

Lists the serial/USB interfaces at the bench (debug probes, UART adapters, CAN
dongles) and cross-maps each against the bench-parts assets assigned to the
active engagement, so the operator can see "ttyACM0 is the J-Link assigned to
R26-0004."

The cross-map is deliberately conservative. VID:PID can't tell two identical
probes apart, so the panel auto-matches only on **serial number** and otherwise
lets the operator **bind a port to an asset by hand**. Bindings are per session —
unplugging and replugging is a new reality to confirm, not state to persist.

Bench gear and port identifiers carry no target identity, so this panel is
stream-safe (not tagged sensitive): it is the engagement/console/findings panels
that stay off air.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from benchhud.assets.source import Asset, AssetSource
from benchhud.devices.discovery import UsbInterface, list_usb_interfaces
from benchhud.shell.panel import PANEL_MARGINS, Panel

AssetSourceProvider = Callable[[], AssetSource]
ActiveRefProvider = Callable[[], str | None]
InterfacesProvider = Callable[[], list[UsbInterface]]

_DASH = "—"


def auto_match(interface: UsbInterface, assets: Sequence[Asset]) -> Asset | None:
    """The assigned asset whose serial matches this port's serial, or None.

    Serial only — VID:PID is shared by identical probes, so matching on it would
    bind the wrong one. A port with no serial number never auto-matches.
    """
    serial = (interface.serial_number or "").strip().lower()
    if not serial:
        return None
    return next((a for a in assets if a.serial.strip().lower() == serial), None)


class InterfacePanel(Panel):
    panel_id = "interfaces"
    panel_title = "Interfaces"
    # Stream-safe: bench gear + port ids carry no target identity (not sensitive).

    def __init__(
        self,
        asset_source: AssetSourceProvider,
        active_ref: ActiveRefProvider | None = None,
        interfaces: InterfacesProvider = list_usb_interfaces,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_source = asset_source
        self._active_ref = active_ref
        self._interfaces = interfaces
        #: Operator port→asset bindings for this session (device path → asset id).
        self._bindings: dict[str, int] = {}
        self._assets: list[Asset] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addLayout(self._build_header())

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Port", "Description", "VID:PID", "Serial", "Asset"])
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumWidth(1)  # never force a wide dock (#230); it scrolls
        self.tree.currentItemChanged.connect(lambda *_: self._sync_bind_row())
        layout.addWidget(self.tree)

        layout.addLayout(self._build_bind_row())

        self.message_label = QLabel()
        self.message_label.setObjectName("InterfaceMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setVisible(False)
        layout.addWidget(self.message_label)

        self.refresh()

    # --- construction ----------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        self.ref_label = QLabel(_DASH)
        # Let the ref shrink with a narrow dock rather than forcing it wide (#230).
        self.ref_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.ref_label.setMinimumWidth(1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        row = QHBoxLayout()
        row.addWidget(QLabel("Engagement"))
        row.addWidget(self.ref_label, stretch=1)
        row.addWidget(self.refresh_button)
        return row

    def _build_bind_row(self) -> QHBoxLayout:
        self.asset_combo = QComboBox()
        # Asset labels get long ("J-Link A  · AAA111"); let the combo shrink with
        # the dock instead of forcing it wide (#230).
        self.asset_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.asset_combo.setMinimumWidth(1)
        self.bind_button = QPushButton("Bind")
        self.bind_button.setToolTip("Bind the selected port to this asset for this session")
        self.bind_button.clicked.connect(self.bind_selected)
        self.unbind_button = QPushButton("Unbind")
        self.unbind_button.clicked.connect(self.unbind_selected)
        row = QHBoxLayout()
        row.addWidget(QLabel("Port ="))
        row.addWidget(self.asset_combo, stretch=1)
        row.addWidget(self.bind_button)
        row.addWidget(self.unbind_button)
        return row

    # --- behaviour -------------------------------------------------------

    def refresh(self) -> None:
        """Re-scan interfaces and re-read the active engagement's assigned gear."""
        source = self._asset_source()
        ref = self._active_ref() if self._active_ref is not None else None
        self._assets = source.list_assets(assigned_ref=ref) if ref else []
        interfaces = self._interfaces()

        self.ref_label.setText(ref or "— (no engagement)")
        self.tree.clear()
        for interface in interfaces:
            self.tree.addTopLevelItem(self._row(interface))
        for column in range(5):
            self.tree.resizeColumnToContents(column)

        self._populate_combo()
        self._show_message(interfaces, source_name=source.name, ref=ref)
        self._sync_bind_row()

    def _row(self, interface: UsbInterface) -> QTreeWidgetItem:
        asset, how = self._resolved(interface)
        if asset is not None:
            asset_text = f"{asset.name} ({how})"
        elif interface.device in self._bindings:
            # Bound by hand to something not in the current engagement's gear.
            asset_text = f"asset #{self._bindings[interface.device]} (not in engagement)"
        else:
            asset_text = _DASH
        item = QTreeWidgetItem(
            [
                interface.device,
                interface.description,
                interface.vid_pid or _DASH,
                interface.serial_number or _DASH,
                asset_text,
            ]
        )
        item.setData(0, Qt.ItemDataRole.UserRole, interface)
        return item

    def _resolved(self, interface: UsbInterface) -> tuple[Asset | None, str]:
        """The asset bound to this interface and how: manual wins over auto."""
        asset_id = self._bindings.get(interface.device)
        if asset_id is not None:
            return next((a for a in self._assets if a.id == asset_id), None), "manual"
        return auto_match(interface, self._assets), "auto"

    def _populate_combo(self) -> None:
        self.asset_combo.clear()
        for asset in self._assets:
            label = asset.name
            if asset.serial:
                label += f"  · {asset.serial}"
            self.asset_combo.addItem(label, asset)

    def bind_selected(self) -> None:
        interface = self.selected_interface()
        asset = self.asset_combo.currentData()
        if interface is None or asset is None:
            return
        self._bindings[interface.device] = asset.id
        self.refresh()

    def unbind_selected(self) -> None:
        interface = self.selected_interface()
        if interface is not None and self._bindings.pop(interface.device, None) is not None:
            self.refresh()

    def selected_interface(self) -> UsbInterface | None:
        item = self.tree.currentItem()
        return None if item is None else item.data(0, Qt.ItemDataRole.UserRole)

    def _sync_bind_row(self) -> None:
        interface = self.selected_interface()
        has_assets = self.asset_combo.count() > 0
        self.bind_button.setEnabled(interface is not None and has_assets)
        self.unbind_button.setEnabled(interface is not None and interface.device in self._bindings)

    def _show_message(
        self, interfaces: Sequence[UsbInterface], *, source_name: str, ref: str | None
    ) -> None:
        if not interfaces:
            self._message("No serial/USB interfaces detected.")
        elif source_name == "none":
            self._message("No asset backend configured — ports shown without gear mapping.")
        elif ref is None:
            self._message("Import an engagement to map ports to its assigned gear.")
        elif not self._assets:
            self._message(f"No gear assigned to {ref} in bench-parts.")
        else:
            self.message_label.setVisible(False)

    def _message(self, text: str) -> None:
        self.message_label.setText(text)
        self.message_label.setVisible(True)
