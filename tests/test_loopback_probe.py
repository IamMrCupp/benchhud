"""Why the loopback node is missing (#240), against a fake /sys."""

from __future__ import annotations

import pytest

from benchhud.config import Config
from benchhud.output import loopback_probe
from benchhud.output import sink as sink_module
from benchhud.output.loopback_probe import (
    LoopbackNode,
    configured_video_nrs,
    diagnose,
    loopback_nodes,
    module_loaded,
)
from benchhud.shell.preferences import PreferencesDialog


@pytest.fixture(autouse=True)
def no_real_modprobe_d(tmp_path, monkeypatch) -> None:
    """Never read this machine's /etc/modprobe.d: the bench has a real options line."""
    monkeypatch.setattr(loopback_probe, "MODPROBE_DIR", str(tmp_path / "no-modprobe.d"))


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


def fake_modprobe(tmp_path, *lines):
    """An /etc/modprobe.d holding one benchhud-loopback.conf."""
    conf_dir = tmp_path / "modprobe.d"
    conf_dir.mkdir()
    (conf_dir / "benchhud-loopback.conf").write_text("\n".join(lines) + "\n")
    return conf_dir


FOUR = (
    "options v4l2loopback devices=4 video_nr=10,11,12,13 "
    "card_label=benchhud,OBS-Virtual-Camera,benchhud-scope,benchhud-thermal "
    "exclusive_caps=1,1,1,1"
)


def test_configured_video_nrs_reads_the_options_line(tmp_path) -> None:
    conf_dir = fake_modprobe(tmp_path, "# loopback nodes", "options uvcvideo nodrop=0", FOUR)
    assert configured_video_nrs(conf_dir) == [10, 11, 12, 13]
    assert configured_video_nrs(tmp_path / "missing") == []


def test_stale_initramfs_options_are_named(tmp_path) -> None:
    """The bench (#366): the file asks for four nodes, the module came up with two."""
    root = fake_sys(tmp_path, loopback=[("video10", "benchhud"), ("video11", "OBS-Virtual-Camera")])
    reason = diagnose("/dev/video12", root, fake_modprobe(tmp_path, FOUR))
    assert reason.startswith("/dev/video12 doesn't exist.")
    assert "2 of the 4 nodes" in reason
    assert "sudo update-initramfs -u" in reason


def test_a_node_modprobe_never_asked_for_keeps_the_old_hint(tmp_path) -> None:
    root = fake_sys(tmp_path, loopback=[("video10", "benchhud")])
    reason = diagnose("/dev/video20", root, fake_modprobe(tmp_path, FOUR))
    assert "update-initramfs" not in reason and "Pick one in Preferences" in reason


def test_obs_loading_it_first_is_not_blamed_on_the_initramfs(tmp_path) -> None:
    """modprobe.d asks for video10, but the only node is OBS's own: the old hint stands."""
    root = fake_sys(tmp_path, loopback=[("video5", "OBS Virtual Camera")])
    reason = diagnose("/dev/video10", root, fake_modprobe(tmp_path, FOUR))
    assert "update-initramfs" not in reason and "a node of its own" in reason
