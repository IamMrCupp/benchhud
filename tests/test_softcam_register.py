"""Finding and registering the Windows virtual camera's DLL, without Windows.

The registry, the elevated ``regsvr32`` run and the platform are all injected,
so the decisions are pinned here on any OS. The real registry and the real
``regsvr32`` run in CI's ``softcam`` and ``msi-verify`` jobs.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from benchhud import app
from benchhud.config import Config
from benchhud.output import softcam, softcam_register
from benchhud.output.softcam import (
    SOFTCAM_CLSID,
    SOFTCAM_DLL_ENV,
    SOFTCAM_DLL_MISSING,
    SOFTCAM_NOT_REGISTERED,
    SoftcamNotRegistered,
    SoftcamUnavailable,
    bundled_dll_path,
    open_softcam_camera,
    open_softcam_sink,
    registered_dll_path,
)
from benchhud.output.softcam_register import (
    RegistrationError,
    SoftcamStatus,
    register,
    unregister,
)

# --- what Windows has registered --------------------------------------------


def _fake_winreg(value: str | None) -> types.SimpleNamespace:
    """A ``winreg`` whose softcam key holds ``value`` (``None``: no such key)."""
    opened: list[tuple[object, str, int]] = []

    class _Key:
        def __enter__(self) -> _Key:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    def open_key(root: object, path: str, _reserved: int, access: int) -> _Key:
        opened.append((root, path, access))
        if value is None:
            raise FileNotFoundError(path)
        return _Key()

    return types.SimpleNamespace(
        HKEY_CLASSES_ROOT="HKCR",
        KEY_READ=0x20019,
        KEY_WOW64_64KEY=0x0100,
        OpenKey=open_key,
        QueryValueEx=lambda _key, _name: (value, 1),
        opened=opened,
    )


def test_no_registry_means_nothing_registered(monkeypatch) -> None:
    """Off Windows there is no ``winreg`` at all."""
    monkeypatch.setitem(sys.modules, "winreg", None)
    assert registered_dll_path() is None


def test_registered_path_is_read_from_softcams_class_key(monkeypatch) -> None:
    winreg = _fake_winreg(r"C:\Program Files\benchhud\app\softcam.dll")
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    assert registered_dll_path() == Path(r"C:\Program Files\benchhud\app\softcam.dll")
    root, path, access = winreg.opened[0]
    assert root == "HKCR"
    assert path == rf"CLSID\{SOFTCAM_CLSID}\InprocServer32"
    # The 64-bit view: a 32-bit registration is a DLL benchhud can't load.
    assert access & winreg.KEY_WOW64_64KEY


def test_a_missing_key_means_nothing_registered(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(None))
    assert registered_dll_path() is None


def test_an_empty_value_means_nothing_registered(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(""))
    assert registered_dll_path() is None


def test_the_clsid_is_softcams() -> None:
    """``CLSID_DShowSoftcam`` in softcam's ``softcam.cpp``, v1.8.1."""
    assert SOFTCAM_CLSID == "{AEF3B972-5FA5-4647-9571-358EB472BC9E}"


# --- the DLL this benchhud came with ----------------------------------------


def test_the_env_var_names_a_dll(monkeypatch, tmp_path) -> None:
    dll = tmp_path / "softcam.dll"
    dll.write_bytes(b"MZ")
    monkeypatch.setenv(SOFTCAM_DLL_ENV, str(dll))
    assert bundled_dll_path() == dll


def test_an_env_var_naming_nothing_is_passed_over(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(SOFTCAM_DLL_ENV, str(tmp_path / "nope.dll"))
    assert bundled_dll_path() != tmp_path / "nope.dll"


# --- opening: registered or not ---------------------------------------------


def test_opening_refuses_when_nothing_is_registered(monkeypatch) -> None:
    """softcam would take the frames anyway, and no camera app could see them."""
    monkeypatch.setattr(softcam, "registered_dll_path", lambda: None)
    with pytest.raises(SoftcamNotRegistered):
        open_softcam_camera(1280, 720)


def test_unregistered_gets_the_reason_that_names_the_fix(monkeypatch) -> None:
    monkeypatch.setattr(softcam, "registered_dll_path", lambda: None)
    with pytest.raises(SoftcamUnavailable) as caught:
        open_softcam_sink(1280, 720)
    assert caught.value.reason == SOFTCAM_NOT_REGISTERED
    assert "Preferences" in caught.value.reason


def test_a_registration_pointing_at_nothing_is_a_missing_dll(monkeypatch, tmp_path) -> None:
    """Left behind by a deleted install: registering again is not the fix."""
    monkeypatch.setattr(softcam, "registered_dll_path", lambda: tmp_path / "gone.dll")
    with pytest.raises(SoftcamUnavailable) as caught:
        open_softcam_sink(1280, 720)
    assert caught.value.reason.startswith(SOFTCAM_DLL_MISSING)
    assert "gone.dll" in caught.value.reason


# --- status -----------------------------------------------------------------


def test_status_is_ready_only_when_the_registered_file_exists(tmp_path) -> None:
    dll = tmp_path / "softcam.dll"
    dll.write_bytes(b"MZ")
    assert SoftcamStatus(registered=dll, bundled=dll).ready
    assert not SoftcamStatus(registered=tmp_path / "gone.dll", bundled=dll).ready
    assert not SoftcamStatus(registered=None, bundled=dll).ready


def test_status_describes_each_case(tmp_path) -> None:
    dll = tmp_path / "softcam.dll"
    dll.write_bytes(b"MZ")
    assert SoftcamStatus(dll, dll).describe() == f"registered ({dll})"
    assert SoftcamStatus(None, dll).describe() == "not registered"
    assert "no softcam.dll" in SoftcamStatus(None, None).describe()
    assert "missing" in SoftcamStatus(tmp_path / "gone.dll", dll).describe()


# --- register / unregister --------------------------------------------------


class _Run:
    """Stands in for the elevated run: records the call, returns an exit code."""

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, executable: str, arguments) -> int:
        self.calls.append((executable, list(arguments)))
        return self.code


def _dll(tmp_path: Path) -> Path:
    dll = tmp_path / "softcam.dll"
    dll.write_bytes(b"MZ")
    return dll


def test_register_runs_regsvr32_on_the_dll(tmp_path) -> None:
    dll, run = _dll(tmp_path), _Run()
    assert register(dll, run=run, registered=lambda: dll) == dll
    assert run.calls == [("regsvr32.exe", ["/s", str(dll)])]


def test_register_without_a_dll_says_so_and_runs_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(softcam_register, "bundled_dll_path", lambda: None)
    run = _Run()
    with pytest.raises(RegistrationError) as caught:
        register(run=run, registered=lambda: None)
    assert caught.value.reason == SOFTCAM_DLL_MISSING
    assert run.calls == []


def test_register_reports_regsvr32s_exit_code(tmp_path) -> None:
    with pytest.raises(RegistrationError) as caught:
        register(_dll(tmp_path), run=_Run(code=5), registered=lambda: None)
    assert "exit code 5" in caught.value.reason
    assert "administrator" in caught.value.reason


def test_register_checks_the_registry_not_just_the_exit_code(tmp_path) -> None:
    """regsvr32 saying yes is not a camera app finding the camera."""
    with pytest.raises(RegistrationError) as caught:
        register(_dll(tmp_path), run=_Run(code=0), registered=lambda: None)
    assert "no registration" in caught.value.reason


def test_unregister_with_nothing_registered_runs_nothing() -> None:
    run = _Run()
    assert unregister(run=run, registered=lambda: None) is False
    assert run.calls == []


def test_unregister_runs_regsvr32_on_the_registered_dll(tmp_path) -> None:
    dll, run = _dll(tmp_path), _Run()
    answers = iter([dll, None])  # registered before, gone after
    assert unregister(run=run, registered=lambda: next(answers)) is True
    assert run.calls == [("regsvr32.exe", ["/u", "/s", str(dll)])]


def test_unregister_says_when_the_registration_survived(tmp_path) -> None:
    dll = _dll(tmp_path)
    with pytest.raises(RegistrationError) as caught:
        unregister(run=_Run(), registered=lambda: dll)
    assert "still there" in caught.value.reason


# --- benchhud virtual-camera ------------------------------------------------


def test_off_windows_the_command_says_it_does_not_apply(capsys) -> None:
    assert softcam_register.main(["status"], platform="linux") == 0
    assert softcam_register.main(["register"], platform="darwin") == 1
    assert "only applies on Windows" in capsys.readouterr().out


def test_status_exits_zero_only_when_ready(monkeypatch, capsys, tmp_path) -> None:
    dll = _dll(tmp_path)
    monkeypatch.setattr(softcam_register, "status", lambda: SoftcamStatus(dll, dll))
    assert softcam_register.main(["status"], platform="win32") == 0
    assert str(dll) in capsys.readouterr().out
    monkeypatch.setattr(softcam_register, "status", lambda: SoftcamStatus(None, dll))
    assert softcam_register.main(["status"], platform="win32") == 1
    assert capsys.readouterr().out.strip() == "not registered"


def test_a_failed_register_goes_to_stderr_with_exit_one(monkeypatch, capsys) -> None:
    def refuse() -> Path:
        raise RegistrationError("administrator approval was declined")

    monkeypatch.setattr(softcam_register, "register", refuse)
    assert softcam_register.main(["register"], platform="win32") == 1
    assert "declined" in capsys.readouterr().err


def test_the_app_answers_virtual_camera_without_a_window(monkeypatch) -> None:
    """Dispatched before Qt is imported, like ``--version`` and ``config``."""
    assert "virtual-camera" in app.COMMANDS
    monkeypatch.setattr(softcam_register, "main", lambda argv: 7 if argv == ["status"] else 0)
    assert app.main(["virtual-camera", "status"]) == 7


# --- Preferences ------------------------------------------------------------


class _NoDevices:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


def _dialog(qtbot, platform: str, state: SoftcamStatus, register_call=None):
    from benchhud.shell.preferences import PreferencesDialog

    dialog = PreferencesDialog(
        Config(),
        _NoDevices(),
        platform=platform,
        virtual_camera_status=lambda: state,
        virtual_camera_register=register_call or (lambda: Path("softcam.dll")),
    )
    qtbot.addWidget(dialog)
    return dialog


def test_only_windows_gets_the_register_button(qtbot, tmp_path) -> None:
    state = SoftcamStatus(None, _dll(tmp_path))
    assert hasattr(_dialog(qtbot, "win32", state), "virtual_camera_button")
    assert not hasattr(_dialog(qtbot, "linux", state), "virtual_camera_button")
    assert not hasattr(_dialog(qtbot, "darwin", state), "virtual_camera_button")


def test_the_button_is_live_only_when_there_is_something_to_do(qtbot, tmp_path) -> None:
    dll = _dll(tmp_path)
    assert _dialog(qtbot, "win32", SoftcamStatus(None, dll)).virtual_camera_button.isEnabled()
    assert not _dialog(qtbot, "win32", SoftcamStatus(dll, dll)).virtual_camera_button.isEnabled()
    assert not _dialog(qtbot, "win32", SoftcamStatus(None, None)).virtual_camera_button.isEnabled()


def test_registering_from_preferences_shows_the_new_state(qtbot, tmp_path) -> None:
    dll = _dll(tmp_path)
    states = iter([SoftcamStatus(None, dll), SoftcamStatus(dll, dll)])
    calls: list[str] = []
    from benchhud.shell.preferences import PreferencesDialog

    dialog = PreferencesDialog(
        Config(),
        _NoDevices(),
        platform="win32",
        virtual_camera_status=lambda: next(states),
        virtual_camera_register=lambda: calls.append("register") or dll,
    )
    qtbot.addWidget(dialog)
    assert dialog.virtual_camera_state.text() == "not registered"
    dialog.virtual_camera_button.click()
    assert calls == ["register"]
    assert dialog.virtual_camera_state.text().startswith(f"registered ({dll})")
    assert not dialog.virtual_camera_button.isEnabled()


def test_a_declined_prompt_is_shown_and_the_button_stays(qtbot, tmp_path) -> None:
    def refuse() -> Path:
        raise RegistrationError("administrator approval was declined")

    dialog = _dialog(qtbot, "win32", SoftcamStatus(None, _dll(tmp_path)), refuse)
    dialog.virtual_camera_button.click()
    assert "declined" in dialog.virtual_camera_state.text()
    assert dialog.virtual_camera_button.isEnabled()
