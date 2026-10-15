"""Why the loopback node is missing (#240), against a fake /sys."""

from __future__ import annotations

from benchhud.config import Config
from benchhud.output import sink as sink_module
from benchhud.output.loopback_probe import LoopbackNode, diagnose, loopback_nodes, module_loaded
from benchhud.shell.preferences import PreferencesDialog


def fake_sys(tmp_path, *, loaded=True, loopback=(), cameras=()):
    """A /sys with the module (or not), loopback nodes, and USB camera nodes."""
    root = tmp_path / "sys"
    if loaded:
        (root / "module" / "v4l2loopback").mkdir(parents=True)
    for node, label in loopback:
        path = root / "devices" / "virtual" / "video4linux" / node
        path.mkdir(parents=True)
        (path / "name").write_text(label + "\n")
        (root / "class" / "video4linux" / node).mkdir(parents=True)
    for node in cameras:
        (root / "class" / "video4linux" / node).mkdir(parents=True)
    return root


def test_not_loaded_says_how_to_load_it(tmp_path) -> None:
    root = fake_sys(tmp_path, loaded=False)
    assert not module_loaded(root)
    reason = diagnose("/dev/video10", root)
    assert "isn't loaded" in reason and "sudo modprobe v4l2loopback" in reason


def test_obs_loaded_it_first(tmp_path) -> None:
    """The bench: OBS's node at /dev/video5, ours never created."""
    root = fake_sys(tmp_path, loopback=[("video5", "OBS Virtual Camera")], cameras=["video0"])
    assert loopback_nodes(root) == [LoopbackNode("/dev/video5", "OBS Virtual Camera")]
    reason = diagnose("/dev/video10", root)
    assert reason.startswith("/dev/video10 doesn't exist.")
    assert "/dev/video5 (OBS Virtual Camera)" in reason
    assert "a node of its own" in reason


def test_another_loopback_node_is_offered_without_the_obs_note(tmp_path) -> None:
    root = fake_sys(tmp_path, loopback=[("video11", "benchhud")])
    reason = diagnose("/dev/video10", root)
    assert "/dev/video11 (benchhud)" in reason and "a node of its own" not in reason


def test_a_real_camera_is_not_a_loopback_node(tmp_path) -> None:
    root = fake_sys(tmp_path, loopback=[("video10", "benchhud")], cameras=["video0"])
    assert diagnose("/dev/video0", root).startswith("/dev/video0 is a camera")


def test_loaded_with_no_nodes(tmp_path) -> None:
    root = fake_sys(tmp_path)
    assert "has no nodes" in diagnose("/dev/video10", root)


def test_the_right_node_needs_no_diagnosis(tmp_path) -> None:
    root = fake_sys(tmp_path, loopback=[("video10", "benchhud")])
    assert diagnose("/dev/video10", root) is None


def test_nodes_sort_numerically(tmp_path) -> None:
    root = fake_sys(tmp_path, loopback=[("video11", "b"), ("video9", "a"), ("video10", "c")])
    assert [n.path for n in loopback_nodes(root)] == ["/dev/video9", "/dev/video10", "/dev/video11"]


def test_the_sink_reports_the_diagnosis(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sink_module.sys, "platform", "linux")
    monkeypatch.setattr(sink_module, "diagnose_loopback", lambda device: f"{device}: diagnosed")
    out = sink_module.open_output_sink(str(tmp_path / "video10"), 64, 48)
    assert out.reason.endswith("video10: diagnosed")


def test_the_sink_keeps_the_raw_error_when_theres_nothing_to_add(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sink_module.sys, "platform", "linux")
    monkeypatch.setattr(sink_module, "diagnose_loopback", lambda device: None)
    out = sink_module.open_output_sink(str(tmp_path / "video10"), 64, 48)
    assert "unavailable" in out.reason


class _NoDevices:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


def test_preferences_offer_the_loopback_nodes(qtbot) -> None:
    config = Config()
    nodes = [LoopbackNode("/dev/video5", "OBS Virtual Camera"), LoopbackNode("/dev/video10", "b")]
    dialog = PreferencesDialog(config, _NoDevices(), loopback_nodes=lambda: nodes, platform="linux")
    qtbot.addWidget(dialog)
    combo = dialog.output_device
    assert [combo.itemText(i) for i in range(combo.count())] == ["/dev/video5", "/dev/video10"]
    assert combo.currentText() == "/dev/video10"  # the configured one stays selected
    combo.setCurrentText("/dev/video5")
    dialog.apply_to_config()
    assert config.output.device == "/dev/video5"


def test_preferences_hide_the_device_where_there_is_none(qtbot) -> None:
    dialog = PreferencesDialog(Config(), _NoDevices(), loopback_nodes=list, platform="darwin")
    qtbot.addWidget(dialog)
    dialog.show()
    assert not dialog.output_device.isVisible()
