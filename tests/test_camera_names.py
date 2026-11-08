"""Fixed /dev/cam-* names for OBS, and who holds a camera (#325)."""

from __future__ import annotations

import os

from benchhud import app
from benchhud.capture.fallback import FallbackFrameSource
from benchhud.devices import camnames
from benchhud.devices.camnames import (
    CameraId,
    name_cameras,
    parse_by_id,
    rule_for,
    rules_file,
    slug,
    udev_rules_main,
    usb_cameras,
)
from benchhud.devices.holders import held_by, holders
from benchhud.shell.main_window import camera_holder_check

THERMAL = "Sipeed_Ltd._Thermal_Camera__UVC__0123456789"
SCOPE = "YW3609A_YW3609A_985200000000100"
C922 = "046d_C922_Pro_Stream_Webcam_ABCDEF12"


def fake_dev(tmp_path):
    """A /dev with three USB cameras (each with a metadata node) and their by-id links."""
    dev = tmp_path / "dev"
    by_id = dev / "v4l" / "by-id"
    by_id.mkdir(parents=True)
    for n in range(6):
        (dev / f"video{n}").touch()
    (dev / "video10").touch()  # v4l2loopback: no by-id link
    for serial, node in ((SCOPE, 0), (C922, 2), (THERMAL, 4)):
        os.symlink(dev / f"video{node}", by_id / f"usb-{serial}-video-index0")
        os.symlink(dev / f"video{node + 1}", by_id / f"usb-{serial}-video-index1")
    labels = {
        str(dev / "video0"): "YW3609A",
        str(dev / "video2"): "C922 Pro Stream Webcam",
        str(dev / "video4"): "Thermal Camera (UVC)",
    }
    return dev, by_id, labels.get


def test_parse_by_id() -> None:
    assert parse_by_id(f"usb-{THERMAL}-video-index0") == ("usb", THERMAL, 0)
    assert parse_by_id(f"usb-{THERMAL}-video-index1") == ("usb", THERMAL, 1)
    assert parse_by_id("not-a-link") is None


def test_slug() -> None:
    assert slug("C922 Pro Stream Webcam") == "c922-pro-stream-webcam"
    assert slug("Thermal Camera (UVC)") == "thermal-camera-uvc"
    assert slug("***") == ""


def test_usb_cameras_lists_capture_nodes_only(tmp_path) -> None:
    _, by_id, label_of = fake_dev(tmp_path)
    found = usb_cameras(str(by_id), label_of)
    assert {c.serial for c in found} == {SCOPE, C922, THERMAL}
    assert all(c.link.endswith("-video-index0") for c in found)
    assert {c.label for c in found} == {"YW3609A", "C922 Pro Stream Webcam", "Thermal Camera (UVC)"}
    assert usb_cameras(str(tmp_path / "missing")) == []


def test_unlabelled_camera_falls_back_to_its_serial(tmp_path) -> None:
    _, by_id, _ = fake_dev(tmp_path)
    found = usb_cameras(str(by_id), lambda _node: None)
    assert {c.label for c in found} == {SCOPE, C922, THERMAL}


def test_configured_cameras_take_their_role(tmp_path) -> None:
    dev, by_id, label_of = fake_dev(tmp_path)
    cameras = usb_cameras(str(by_id), label_of)
    # The config can hold a /dev/videoN or a by-id link; both resolve to the camera.
    roles = {
        "scope": str(dev / "video0"),
        "thermal": str(by_id / f"usb-{THERMAL}-video-index0"),
    }
    names = {n.camera.serial: n.path for n in name_cameras(cameras, roles)}
    assert names == {
        SCOPE: "/dev/cam-scope",
        THERMAL: "/dev/cam-thermal",
        C922: "/dev/cam-c922-pro-stream-webcam",
    }


def test_unconfigured_role_names_by_model(tmp_path) -> None:
    _, by_id, label_of = fake_dev(tmp_path)
    cameras = usb_cameras(str(by_id), label_of)
    names = {n.name for n in name_cameras(cameras, {"scope": "", "thermal": "/dev/nothing"})}
    assert names == {"yw3609a", "c922-pro-stream-webcam", "thermal-camera-uvc"}


def test_same_model_twice_gets_unique_names() -> None:
    a = CameraId("/a", "usb", "Cam_1", "Webcam")
    b = CameraId("/b", "usb", "Cam_2", "Webcam")
    assert [n.name for n in name_cameras([a, b], {})] == ["webcam", "webcam-2"]


def test_rule_matches_what_the_by_id_link_matches() -> None:
    camera = CameraId("/l", "usb", THERMAL, "Thermal Camera (UVC)")
    (named,) = name_cameras([camera], {"thermal": "/l"}, same=lambda a, b: a == b)
    assert rule_for(named) == (
        'SUBSYSTEM=="video4linux", ENV{ID_BUS}=="usb", '
        f'ENV{{ID_SERIAL}}=="{THERMAL}", ATTR{{index}}=="0", SYMLINK+="cam-thermal"'
    )
    text = rules_file([named])
    assert camnames.RULES_PATH in text
    assert text.endswith('SYMLINK+="cam-thermal"\n')
    # Every line is a comment, blank, or a rule; udev rejects anything else.
    for line in text.splitlines():
        assert not line or line.startswith("#") or line.startswith('SUBSYSTEM=="video4linux"')


def test_rules_file_sorts_after_persistent_v4l() -> None:
    # ID_SERIAL is set by systemd's 60-persistent-v4l.rules.
    assert os.path.basename(camnames.RULES_PATH) > "60-persistent-v4l.rules"


def test_udev_rules_command(tmp_path) -> None:
    dev, by_id, label_of = fake_dev(tmp_path)
    out: list[str] = []
    code = udev_rules_main(
        out=out.append,
        err=out.append,
        platform="linux",
        cameras=lambda: usb_cameras(str(by_id), label_of),
        roles=lambda: {"scope": str(dev / "video0"), "thermal": str(dev / "video4")},
    )
    assert code == 0
    assert 'SYMLINK+="cam-scope"' in out[0]
    assert 'SYMLINK+="cam-thermal"' in out[0]


def test_udev_rules_command_refuses_off_linux_and_with_no_cameras() -> None:
    err: list[str] = []
    assert udev_rules_main(out=err.append, err=err.append, platform="darwin") == 1
    assert "Linux only" in err[-1]
    assert (
        udev_rules_main(out=err.append, err=err.append, platform="linux", cameras=list, roles=dict)
        == 1
    )
    assert "no USB cameras" in err[-1]


def test_app_dispatches_udev_rules(monkeypatch) -> None:
    monkeypatch.setattr(camnames, "udev_rules_main", lambda: 7)
    assert app.main(["udev-rules"]) == 7


# --- who holds a camera ------------------------------------------------------


def fake_proc(tmp_path, device):
    """A /proc where obs (pid 4242) has ``device`` open and pid 77 doesn't."""
    proc = tmp_path / "proc"
    for pid, comm, target in ((4242, "obs", device), (77, "bash", "/dev/null"), (1, "init", None)):
        base = proc / str(pid)
        (base / "fd").mkdir(parents=True)
        (base / "comm").write_text(comm + "\n")
        if target is not None:
            os.symlink(target, base / "fd" / "3")
    os.chmod(proc / "1" / "fd", 0)  # another user's process: unreadable
    (proc / "self").mkdir()  # not a pid
    return proc


def test_holders_names_the_process(tmp_path) -> None:
    dev, by_id, _ = fake_dev(tmp_path)
    proc = fake_proc(tmp_path, str(dev / "video4"))
    try:
        # Asked by its by-id link, the holder's fd points at the real node.
        link = str(by_id / f"usb-{THERMAL}-video-index0")
        assert [(h.pid, h.name) for h in holders(link, str(proc), self_pid=1)] == [(4242, "obs")]
        assert held_by(link, str(proc), self_pid=1) == "held by obs (pid 4242)"
        assert held_by(str(dev / "video0"), str(proc), self_pid=1) is None
        # Our own open of the camera doesn't count.
        assert held_by(link, str(proc), self_pid=4242) is None
    finally:
        os.chmod(proc / "1" / "fd", 0o755)


def test_holders_without_a_path_or_proc(tmp_path) -> None:
    assert holders("0") == []
    assert holders("/dev/video4", str(tmp_path / "noproc")) == []


class _ClosedCamera:
    is_open = False
    size = (192, 256)

    def open(self) -> bool:
        return False

    def read(self):
        return None

    def release(self) -> None: ...

    def close(self) -> None: ...


def test_fallback_says_who_holds_the_camera() -> None:
    held = FallbackFrameSource(
        _ClosedCamera(),
        None,
        name="Thermal Camera (UVC)",
        open_hint="check the video group",
        held_by=lambda: "held by obs (pid 4242)",
    )
    assert held.status.reason == "couldn't open Thermal Camera (UVC) — held by obs (pid 4242)"
    free = FallbackFrameSource(
        _ClosedCamera(),
        None,
        name="thermal",
        open_hint="check the video group",
        held_by=lambda: None,
    )
    assert free.status.reason == "couldn't open thermal — check the video group"


def test_holder_check_only_for_linux_paths() -> None:
    assert camera_holder_check("/dev/video4", "darwin") is None
    assert camera_holder_check("0", "linux") is None
    assert camera_holder_check("", "linux") is None
    assert callable(camera_holder_check("/dev/v4l/by-id/x", "linux"))
