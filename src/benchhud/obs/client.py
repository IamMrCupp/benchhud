"""A small obs-websocket client on Qt's event loop (#455).

It keeps one connection to OBS, tracks the live scene and the scene list, and
can ask for a scene change. OBS may not be running, or may start later, so the
client retries quietly and everything it offers degrades to "not connected".
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from benchhud.obs import protocol

_log = logging.getLogger(__name__)

#: How long to wait before trying OBS again.
RETRY_MS = 5000


def _make_socket() -> Any:
    from PySide6.QtWebSockets import QWebSocket

    return QWebSocket()


class ObsClient(QObject):
    """One connection to an OBS, for reading and choosing the live scene."""

    #: Connected and identified, or not any more.
    connected_changed = Signal(bool)
    #: The live scene changed (also sent once on connecting).
    scene_changed = Signal(str)
    #: The scenes OBS has, in the order OBS lists them.
    scenes_changed = Signal(list)

    def __init__(
        self,
        host: str,
        port: int,
        password: Callable[[], str],
        socket_factory: Callable[[], Any] = _make_socket,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._url = QUrl(f"ws://{host}:{port}")
        self._password = password
        self._socket_factory = socket_factory
        self._socket: Any = None
        self._identified = False
        self._next_id = 0
        self._scene: str | None = None
        self._scenes: list[str] = []
        self._retry = QTimer(self)
        self._retry.setInterval(RETRY_MS)
        self._retry.timeout.connect(self._open)
        self._closing = False

    # --- state ----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._identified

    @property
    def scene(self) -> str | None:
        """The live scene, or None when OBS isn't connected."""
        return self._scene if self._identified else None

    @property
    def scenes(self) -> list[str]:
        return list(self._scenes) if self._identified else []

    # --- lifecycle ------------------------------------------------------

    def start(self) -> None:
        self._closing = False
        self._open()
        self._retry.start()

    def stop(self) -> None:
        self._closing = True
        self._retry.stop()
        if self._socket is not None:
            self._socket.close()
        self._set_identified(False)

    def _open(self) -> None:
        if self._socket is not None or self._closing:
            return
        socket = self._socket_factory()
        socket.textMessageReceived.connect(self._on_message)
        socket.disconnected.connect(self._on_disconnected)
        self._socket = socket
        socket.open(self._url)

    def _on_disconnected(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None and hasattr(socket, "deleteLater"):
            socket.deleteLater()
        self._set_identified(False)

    def _set_identified(self, identified: bool) -> None:
        if identified == self._identified:
            return
        self._identified = identified
        if not identified:
            self._scene = None
            self._scenes = []
        _log.info("OBS %s", "connected" if identified else "disconnected")
        self.connected_changed.emit(identified)

    # --- requests -------------------------------------------------------

    def set_scene(self, name: str) -> bool:
        """Ask OBS to show ``name``. False when OBS isn't connected."""
        if not self._identified or not name:
            return False
        _log.info("OBS scene -> %s", name)
        self._send("SetCurrentProgramScene", {"sceneName": name})
        return True

    def refresh(self) -> None:
        """Ask again for the scene list and the live scene."""
        if self._identified:
            self._send("GetSceneList")

    def _send(self, request_type: str, data: dict[str, Any] | None = None) -> None:
        self._next_id += 1
        self._socket.sendTextMessage(protocol.request(request_type, str(self._next_id), data))

    # --- messages -------------------------------------------------------

    def _on_message(self, text: str) -> None:
        try:
            message = json.loads(text)
            op, data = message["op"], message.get("d") or {}
        except (ValueError, KeyError, TypeError):
            return
        if op == protocol.HELLO:
            # The password is read only now, and never logged.
            self._socket.sendTextMessage(protocol.identify(data, self._password()))
        elif op == protocol.IDENTIFIED:
            self._set_identified(True)
            self.refresh()
        elif op == protocol.RESPONSE:
            self._on_response(data)
        elif op == protocol.EVENT:
            self._on_event(data)

    def _on_response(self, data: dict[str, Any]) -> None:
        status = data.get("requestStatus") or {}
        if not status.get("result", False):
            _log.warning("OBS refused %s: %s", data.get("requestType"), status.get("comment", ""))
            return
        if data.get("requestType") == "GetSceneList":
            body = data.get("responseData") or {}
            # OBS lists scenes bottom-first; show them the way its Scenes dock does.
            names = [s.get("sceneName", "") for s in reversed(body.get("scenes") or [])]
            # The live scene first: whoever hears about the list asks which is on.
            self._set_scene(body.get("currentProgramSceneName"))
            self._set_scenes([n for n in names if n])

    def _on_event(self, data: dict[str, Any]) -> None:
        kind, body = data.get("eventType"), data.get("eventData") or {}
        if kind == "CurrentProgramSceneChanged":
            self._set_scene(body.get("sceneName"))
        elif kind in ("SceneListChanged", "SceneCreated", "SceneRemoved", "SceneNameChanged"):
            self.refresh()

    def _set_scenes(self, names: list[str]) -> None:
        if names != self._scenes:
            self._scenes = names
            self.scenes_changed.emit(list(names))

    def _set_scene(self, name: Any) -> None:
        if isinstance(name, str) and name and name != self._scene:
            self._scene = name
            self.scene_changed.emit(name)
