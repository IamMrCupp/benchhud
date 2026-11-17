"""Register softcam's DLL with Windows, and ``benchhud virtual-camera``.

A camera app finds the Windows virtual camera through a COM registration that
records where ``softcam.dll`` is. Writing it takes administrator rights, so it
happens in one of two places: the installer does it when benchhud is installed
for all users, and otherwise :func:`register` does it on request, with Windows
asking for approval (UAC) once. Both run the same thing, ``regsvr32`` on the
DLL, which calls softcam's own ``DllRegisterServer``.

::

    benchhud virtual-camera status
    benchhud virtual-camera register
    benchhud virtual-camera unregister

Nothing here imports Qt, and the elevated run is injectable, so the logic is
tested on any OS. The real ``regsvr32`` runs in CI's Windows jobs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from benchhud.output.softcam import (
    SOFTCAM_DEVICE_NAME,
    SOFTCAM_DLL_MISSING,
    bundled_dll_path,
    registered_dll_path,
)

#: How long to wait for ``regsvr32`` once it has started. It takes well under a
#: second; this only stops a stuck one from hanging the HUD.
REGSVR32_TIMEOUT_S = 60

#: ``ShellExecuteExW`` fails with this when the UAC prompt is declined.
ERROR_CANCELLED = 1223

#: ``regsvr32``'s own exit codes, for a reason someone can act on.
_REGSVR32_FAILURES = {
    3: "Windows could not load softcam.dll",
    4: "softcam.dll has no registration entry point",
    5: "softcam.dll could not write its registration (not run as administrator?)",
}

#: Runs ``executable`` with ``arguments`` elevated and returns its exit code.
ElevatedRunner = Callable[[str, Sequence[str]], int]


class RegistrationError(RuntimeError):
    """Registering or unregistering didn't happen. ``reason`` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SoftcamStatus:
    """Where the Windows virtual camera stands on this machine."""

    #: The DLL Windows has registered, if any.
    registered: Path | None
    #: The DLL this benchhud came with, if any.
    bundled: Path | None

    @property
    def ready(self) -> bool:
        """Registered, and the registered file is really there."""
        return self.registered is not None and self.registered.is_file()

    def describe(self) -> str:
        if self.ready:
            return f"registered ({self.registered})"
        if self.registered is not None:
            return f"registered at {self.registered}, but that file is missing"
        if self.bundled is None:
            return "not registered, and this benchhud has no softcam.dll to register"
        return "not registered"


def status() -> SoftcamStatus:
    return SoftcamStatus(registered=registered_dll_path(), bundled=bundled_dll_path())


def run_elevated(executable: str, arguments: Sequence[str]) -> int:
    """Run a program as administrator, wait for it, and return its exit code.

    Windows shows its approval prompt unless the caller is already elevated.
    Raises :class:`RegistrationError` if the prompt is declined or the program
    doesn't finish.
    """
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        )

    see_mask_nocloseprocess = 0x00000040
    see_mask_noasync = 0x00000100
    wait_object_0 = 0

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = see_mask_nocloseprocess | see_mask_noasync
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = subprocess.list2cmdline(list(arguments))
    info.nShow = 0  # SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == ERROR_CANCELLED:
            raise RegistrationError("administrator approval was declined")
        raise RegistrationError(f"Windows would not start {executable} (error {error})")
    if not info.hProcess:
        raise RegistrationError(f"Windows started {executable} but gave no process to wait on")
    try:
        waited = kernel32.WaitForSingleObject(info.hProcess, REGSVR32_TIMEOUT_S * 1000)
        if waited != wait_object_0:
            raise RegistrationError(f"{executable} did not finish in {REGSVR32_TIMEOUT_S} s")
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise RegistrationError(f"could not read {executable}'s exit code")
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def _regsvr32(arguments: Sequence[str], run: ElevatedRunner) -> None:
    code = run("regsvr32.exe", arguments)
    if code != 0:
        why = _REGSVR32_FAILURES.get(code, "regsvr32 failed")
        raise RegistrationError(f"{why} (regsvr32 exit code {code})")


def register(
    dll: Path | None = None,
    *,
    run: ElevatedRunner = run_elevated,
    registered: Callable[[], Path | None] = registered_dll_path,
) -> Path:
    """Register softcam with Windows and return the path now on record.

    ``dll`` defaults to the one this benchhud came with. Checks the registry
    afterwards: ``regsvr32`` reporting success is not the same as a camera app
    being able to find the camera.
    """
    dll = dll or bundled_dll_path()
    if dll is None or not dll.is_file():
        raise RegistrationError(SOFTCAM_DLL_MISSING)
    _regsvr32(["/s", str(dll)], run)
    now = registered()
    if now is None:
        raise RegistrationError("regsvr32 reported success, but Windows shows no registration")
    return now


def unregister(
    *,
    run: ElevatedRunner = run_elevated,
    registered: Callable[[], Path | None] = registered_dll_path,
) -> bool:
    """Remove softcam's registration. Returns ``False`` if there was none."""
    dll = registered()
    if dll is None:
        return False
    if not dll.is_file():
        raise RegistrationError(
            f"the registration points at {dll}, which is missing, so it can't unregister itself"
        )
    _regsvr32(["/u", "/s", str(dll)], run)
    if registered() is not None:
        raise RegistrationError("regsvr32 reported success, but the registration is still there")
    return True


def main(argv: Sequence[str], platform: str = sys.platform) -> int:
    """``benchhud virtual-camera <status|register|unregister>``."""
    parser = argparse.ArgumentParser(
        prog="benchhud virtual-camera",
        description=f"The Windows virtual camera ({SOFTCAM_DEVICE_NAME}) that OBS reads.",
    )
    parser.add_argument("action", choices=("status", "register", "unregister"))
    action = parser.parse_args(list(argv)).action

    if not platform.startswith("win"):
        print("virtual-camera only applies on Windows; nothing to register on this system")
        return 0 if action == "status" else 1
    try:
        if action == "status":
            current = status()
            print(current.describe())
            return 0 if current.ready else 1
        if action == "register":
            print(f"registered ({register()})")
            return 0
        print("unregistered" if unregister() else "not registered; nothing to do")
        return 0
    except RegistrationError as exc:
        print(f"error: {exc.reason}", file=sys.stderr)
        return 1
