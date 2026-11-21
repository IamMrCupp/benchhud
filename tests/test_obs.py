"""Choosing OBS's live scene from the HUD (#455)."""

from __future__ import annotations

import json

import pytest

from benchhud.config import Config, ToolConfig
from benchhud.jobs.store import JobStore
from benchhud.obs import protocol
from benchhud.obs.client import ObsClient
from benchhud.obs.tool_scenes import ToolScenes
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.launcher import LauncherPanel
from benchhud.shell.preferences import PreferencesDialog

# --- the wire format ---------------------------------------------------------


def test_identify_answers_a_challenge_only_when_obs_sets_one() -> None:
    plain = json.loads(protocol.identify({"rpcVersion": 1}, "secret"))
    assert plain["op"] == protocol.IDENTIFY
    assert "authentication" not in plain["d"]
    assert plain["d"]["eventSubscriptions"] == protocol.SCENE_EVENTS

    hello = {"authentication": {"salt": "salt", "challenge": "challenge"}}
    answered = json.loads(protocol.identify(hello, "secret"))["d"]["authentication"]
    assert answered == protocol.auth_response("secret", "salt", "challenge")
    assert answered != protocol.auth_response("other", "salt", "challenge")
    assert "secret" not in answered


def test_the_password_comes_from_obs_own_file(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"server_password": "hunter2", "auth_required": True}))
    assert protocol.local_password(path) == "hunter2"

    path.write_text(json.dumps({"server_password": "hunter2", "auth_required": False}))
    assert protocol.local_password(path) == ""

    path.write_text("not json")
    assert protocol.local_password(path) == ""
    assert protocol.local_password(tmp_path / "missing.json") == ""


# --- which scene, and when to go back ----------------------------------------


class _Obs:
    """OBS as ToolScenes sees it: a live scene, and a way to change it."""

    def __init__(self, scene: str | None = "Overhead") -> None:
        self.scene = scene
        self.asked: list[str] = []

    def switch(self, scene: str) -> None:
        self.asked.append(scene)
        self.scene = scene

    def current(self) -> str | None:
        return self.scene


class _Process:
    def __init__(self) -> None:
        self.code: int | None = None

    def poll(self) -> int | None:
        return self.code


@pytest.fixture
def obs() -> _Obs:
    return _Obs()


def test_a_tool_shows_its_scene_and_its_exit_goes_back(obs) -> None:
    scenes, tool = ToolScenes(obs.switch, obs.current), _Process()
    scenes.tool_started("Logic Analyzer", tool)
    assert obs.scene == "Logic Analyzer"

    scenes.poll()
    assert obs.asked == ["Logic Analyzer"]  # still running: nothing more

    tool.code = 0
    scenes.poll()
    assert obs.scene == "Overhead"
    assert not scenes.watching


def test_a_scene_changed_by_hand_is_left_alone(obs) -> None:
    scenes, tool = ToolScenes(obs.switch, obs.current), _Process()
    scenes.tool_started("Logic Analyzer", tool)
    obs.scene = "Scope"  # someone picked another scene meanwhile
    tool.code = 0
    scenes.poll()
    assert obs.scene == "Scope"
    assert obs.asked == ["Logic Analyzer"]


def test_a_tool_started_on_its_own_scene_has_nowhere_to_go_back_to() -> None:
    obs = _Obs("Board View")
    scenes, tool = ToolScenes(obs.switch, obs.current), _Process()
    scenes.tool_started("Board View", tool)
    tool.code = 0
    scenes.poll()
    assert obs.asked == []
    assert obs.scene == "Board View"


def test_the_scene_stays_while_another_window_of_the_tool_is_open(obs) -> None:
    scenes = ToolScenes(obs.switch, obs.current)
    first, second = _Process(), _Process()
    scenes.tool_started("Board View", first)
    scenes.tool_started("Board View", second)
    first.code = 0
    scenes.poll()
    assert obs.scene == "Board View"
    second.code = 0
    scenes.poll()
    assert obs.scene == "Overhead"  # not "Board View", which the second one found showing


def test_a_typed_command_shows_its_scene_and_nothing_brings_it_back(obs) -> None:
    scenes = ToolScenes(obs.switch, obs.current)
    scenes.tool_started("Logic Analyzer", None)
    assert obs.scene == "Logic Analyzer"
    assert not scenes.watching


def test_a_tool_with_no_scene_changes_nothing(obs) -> None:
    scenes = ToolScenes(obs.switch, obs.current)
    scenes.tool_started("", _Process())
    assert obs.asked == [] and not scenes.watching


# --- the connection ----------------------------------------------------------


class _Signal:
    def __init__(self) -> None:
        self._slots = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def emit(self, *args) -> None:
        for slot in list(self._slots):
            slot(*args)


class _Socket:
    """A stand-in for QWebSocket that plays OBS's side by hand."""

    def __init__(self) -> None:
        self.textMessageReceived = _Signal()
        self.disconnected = _Signal()
        self.sent: list[dict] = []
        self.url = None
        self.closed = False

    def open(self, url) -> None:
        self.url = url

    def sendTextMessage(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def close(self) -> None:
        self.closed = True
        self.disconnected.emit()

    def say(self, op: int, data: dict) -> None:
        self.textMessageReceived.emit(json.dumps({"op": op, "d": data}))

    def requests(self, kind: str) -> list[dict]:
        return [
            m["d"]
            for m in self.sent
            if m["op"] == protocol.REQUEST and m["d"]["requestType"] == kind
        ]


SCENES = {
    # OBS lists them bottom-first.
    "scenes": [{"sceneName": "Scope"}, {"sceneName": "Overhead"}, {"sceneName": "Logic Analyzer"}],
    "currentProgramSceneName": "Overhead",
}


def _connect(qtbot, password: str = "pw") -> tuple[ObsClient, _Socket]:
    sockets: list[_Socket] = []

    def factory() -> _Socket:
        sockets.append(_Socket())
        return sockets[-1]

    client = ObsClient("127.0.0.1", 4455, lambda: password, socket_factory=factory)
    client.start()
    socket = sockets[0]
    socket.say(protocol.HELLO, {"authentication": {"salt": "s", "challenge": "c"}})
    socket.say(protocol.IDENTIFIED, {})
    request = socket.requests("GetSceneList")[-1]
    socket.say(
        protocol.RESPONSE,
        {
            "requestType": "GetSceneList",
            "requestId": request["requestId"],
            "requestStatus": {"result": True, "code": 100},
            "responseData": SCENES,
        },
    )
    return client, socket


def test_the_client_identifies_and_learns_the_scenes(qtbot) -> None:
    client, socket = _connect(qtbot)
    identify = next(m for m in socket.sent if m["op"] == protocol.IDENTIFY)
    assert identify["d"]["authentication"] == protocol.auth_response("pw", "s", "c")
    assert client.connected
    assert client.scenes == ["Logic Analyzer", "Overhead", "Scope"]  # as OBS's dock shows them
    assert client.scene == "Overhead"
    client.stop()


def test_the_live_scene_is_known_by_the_time_the_list_is_announced(qtbot) -> None:
    """Found against a real OBS: the list arrived with the live scene still unknown."""
    sockets: list[_Socket] = []
    client = ObsClient(
        "127.0.0.1",
        4455,
        lambda: "",
        socket_factory=lambda: sockets.append(_Socket()) or sockets[-1],
    )
    live_when_listed: list[str | None] = []
    client.scenes_changed.connect(lambda _names: live_when_listed.append(client.scene))
    client.start()
    socket = sockets[0]
    socket.say(protocol.HELLO, {})
    socket.say(protocol.IDENTIFIED, {})
    socket.say(
        protocol.RESPONSE,
        {
            "requestType": "GetSceneList",
            "requestStatus": {"result": True, "code": 100},
            "responseData": SCENES,
        },
    )
    assert live_when_listed == ["Overhead"]
    client.stop()


def test_the_client_follows_scene_changes_and_asks_for_them(qtbot) -> None:
    client, socket = _connect(qtbot)
    seen: list[str] = []
    client.scene_changed.connect(seen.append)

    socket.say(
        protocol.EVENT,
        {"eventType": "CurrentProgramSceneChanged", "eventData": {"sceneName": "Scope"}},
    )
    assert client.scene == "Scope" and seen == ["Scope"]

    assert client.set_scene("Logic Analyzer")
    assert socket.requests("SetCurrentProgramScene")[-1]["requestData"] == {
        "sceneName": "Logic Analyzer"
    }
    client.stop()


def test_without_obs_the_client_knows_nothing_and_refuses_politely(qtbot) -> None:
    client, socket = _connect(qtbot)
    socket.disconnected.emit()
    assert not client.connected
    assert client.scene is None and client.scenes == []
    assert client.set_scene("Scope") is False
    client.stop()


def test_a_refused_request_and_a_garbled_message_are_survived(qtbot) -> None:
    client, socket = _connect(qtbot)
    socket.say(
        protocol.RESPONSE,
        {
            "requestType": "SetCurrentProgramScene",
            "requestStatus": {"result": False, "code": 600, "comment": "no such scene"},
        },
    )
    socket.textMessageReceived.emit("not json")
    assert client.connected and client.scene == "Overhead"
    client.stop()


# --- the Tools dock ----------------------------------------------------------


class _Launcher:
    def __init__(self) -> None:
        self.process = _Process()

    def launch(self, tool, args=()):
        return self.process


def test_the_tools_dock_reports_what_it_started(qtbot) -> None:
    launcher = _Launcher()
    tools = [ToolConfig("PulseView", "pulseview", scene="Logic Analyzer")]
    panel = LauncherPanel(tools, launcher=launcher)
    qtbot.addWidget(panel)
    started: list[tuple] = []
    panel.tool_launched.connect(lambda tool, process: started.append((tool.name, process)))
    panel.buttons["PulseView"].click()
    assert started == [("PulseView", launcher.process)]
    assert "Logic Analyzer" in panel.buttons["PulseView"].toolTip()


def test_the_scene_row_appears_with_scenes_and_asks_for_the_one_picked(qtbot) -> None:
    panel = LauncherPanel([])
    qtbot.addWidget(panel)
    assert panel.scene_combo.parentWidget().isHidden()

    asked: list[str] = []
    panel.scene_requested.connect(asked.append)
    panel.set_scenes(["Overhead", "Scope"], "Scope")
    assert not panel.scene_combo.parentWidget().isHidden()
    assert panel.scene_combo.currentText() == "Scope"
    assert asked == []  # showing the live scene isn't a request for it

    panel.scene_combo.setCurrentIndex(0)
    panel.scene_combo.activated.emit(0)
    assert asked == ["Overhead"]

    panel.set_scenes([])
    assert panel.scene_combo.parentWidget().isHidden()


# --- the whole window --------------------------------------------------------


def _window(qtbot, *, auto_switch: bool) -> tuple[MainWindow, _Socket, _Launcher]:
    config = Config()
    config.obs.enabled = True
    config.obs.auto_switch = auto_switch
    config.tools = [
        ToolConfig("PulseView", "pulseview", scene="Logic Analyzer"),
        ToolConfig("OpenBoardView", "openboardview"),
    ]
    window = MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    launcher = _Launcher()
    window.launcher_panel._launcher = launcher
    made: list[tuple[ObsClient, _Socket]] = []

    def factory() -> ObsClient:
        made.append(_connect(qtbot))
        return made[-1][0]

    window.obs_client_factory = factory
    window.start_obs()
    # The client connected before the window was listening: tell it again.
    window._show_obs_scenes(made[0][0].scenes)
    return window, made[0][1], launcher


def test_starting_a_tool_shows_its_scene_only_when_that_is_switched_on(qtbot) -> None:
    window, socket, _ = _window(qtbot, auto_switch=False)
    window.launcher_panel.buttons["PulseView"].click()
    assert socket.requests("SetCurrentProgramScene") == []
    window.stop_obs()

    window, socket, launcher = _window(qtbot, auto_switch=True)
    window.launcher_panel.buttons["OpenBoardView"].click()  # no scene of its own
    assert socket.requests("SetCurrentProgramScene") == []

    window.launcher_panel.buttons["PulseView"].click()
    asked = socket.requests("SetCurrentProgramScene")
    assert [r["requestData"]["sceneName"] for r in asked] == ["Logic Analyzer"]

    # OBS confirms; the tool exits; the window puts the scene back.
    socket.say(
        protocol.EVENT,
        {"eventType": "CurrentProgramSceneChanged", "eventData": {"sceneName": "Logic Analyzer"}},
    )
    launcher.process.code = 0
    window._poll_tool_scenes()
    asked = socket.requests("SetCurrentProgramScene")
    assert [r["requestData"]["sceneName"] for r in asked] == ["Logic Analyzer", "Overhead"]
    window.stop_obs()


def test_the_window_offers_obs_scenes_and_switches_by_hand(qtbot) -> None:
    window, socket, _ = _window(qtbot, auto_switch=False)
    combo = window.launcher_panel.scene_combo
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Logic Analyzer",
        "Overhead",
        "Scope",
    ]
    assert combo.currentText() == "Overhead"
    window.launcher_panel.scene_requested.emit("Scope")
    assert socket.requests("SetCurrentProgramScene")[-1]["requestData"] == {"sceneName": "Scope"}
    window.stop_obs()
    assert combo.count() == 0


def test_obs_is_left_alone_unless_switched_on(qtbot) -> None:
    window = MainWindow(Config(), job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    window.obs_client_factory = lambda: pytest.fail("connected with [obs] off")
    window.start_obs()


# --- config and Preferences --------------------------------------------------


class _NoDevices:
    """A device scanner for a machine with nothing plugged in."""

    def cameras(self) -> list:
        return []

    def serial_ports(self) -> list:
        return []


def test_config_reads_a_tools_scene_and_the_obs_section() -> None:
    config = Config.from_dict(
        {
            "obs": {"enabled": True, "auto_switch": True, "port": 4460},
            "tools": [{"name": "PulseView", "command": "pulseview", "scene": "Logic Analyzer"}],
        }
    )
    assert config.obs.enabled and config.obs.auto_switch and config.obs.port == 4460
    assert config.tools[0].scene == "Logic Analyzer"
    assert Config().obs.enabled is False and Config().obs.auto_switch is False


def test_preferences_keeps_and_edits_a_tools_scene(qtbot) -> None:
    config = Config()
    config.tools = [ToolConfig("PulseView", "pulseview", scene="Logic Analyzer")]
    dialog = PreferencesDialog(config, _NoDevices())
    qtbot.addWidget(dialog)
    assert dialog.tools_table.item(0, 3).text() == "Logic Analyzer"
    assert not dialog.obs_auto_switch.isEnabled()  # nothing to switch until OBS is on

    dialog.tools_table.item(0, 3).setText("Signals")
    dialog.obs_enabled.setChecked(True)
    dialog.obs_auto_switch.setChecked(True)
    dialog.accept()
    assert config.tools[0].scene == "Signals"
    assert config.obs.enabled and config.obs.auto_switch
