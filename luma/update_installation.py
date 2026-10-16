"""Apply an already verified official Setup to one validated managed install.

No network, registry or shortcut writes happen here. The official installer owns
those transactions. Its immutable versioned payloads and ownership checks keep
loaded DLLs, portable owners, image files and settings out of this helper's scope.
Run the blocking apply function on the updater's worker thread.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Callable

from .installer import InstallerError, MARKER_NAME, installed_executables

SETUP_TIMEOUT = 20 * 60
MAX_SETUP_BYTES = 768 * 1024 * 1024
_STABLE_VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")
_RUNNING_SETUPS: dict[str, object] = {}
_SETUP_LEASES: dict[str, object] = {}
_SETUP_LOCK = threading.Lock()
_SETUP_SPAWN_LOCK = threading.Lock()


class InstallationError(RuntimeError):
    """The current app must stay open; no successful update can be reported."""


class InstallationCancelled(InstallationError):
    pass


class InstallationPending(InstallationError):
    """Setup is still running: do not retry or delete its downloaded executable."""


@dataclass(frozen=True)
class InstallationContext:
    mode: str
    executable: Path
    root: Path | None = None
    version: str | None = None


@dataclass(frozen=True)
class ManagedUpdate:
    version: str
    directory: Path
    executable: Path
    previous_executable: Path
    root: Path


def _windows_dll_api():
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    api.GetDllDirectoryW.restype = wintypes.DWORD
    api.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    api.SetDllDirectoryW.restype = wintypes.BOOL
    return api


def _get_dll_directory(api) -> str:
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    if hasattr(ctypes, "set_last_error"):
        ctypes.set_last_error(0)
    length = api.GetDllDirectoryW(len(buffer), buffer)
    if (length == 0 and hasattr(ctypes, "get_last_error") and ctypes.get_last_error()) or length >= len(buffer):
        raise InstallationError("The process DLL search directory could not be inspected safely.")
    return buffer.value


@contextmanager
def _setup_dll_search_path():
    """Do not let external Setup inherit PyInstaller's old runtime DLL path.

    This is a brief process-global Windows setting change only around Popen,
    serialized across update Setup launches, and restored before any wait/UI work.
    An already-empty setting needs no change, preserving NULL versus empty-string
    semantics which GetDllDirectoryW itself cannot distinguish.
    """
    with _SETUP_SPAWN_LOCK:
        if sys.platform != "win32":
            yield
            return
        api = _windows_dll_api()
        original = _get_dll_directory(api)
        if not original:
            yield
            return
        if not api.SetDllDirectoryW(None):
            raise InstallationError("The old bundled DLL search path could not be cleared for Setup.")
        try:
            yield
        finally:
            if not api.SetDllDirectoryW(original):
                raise InstallationError("The current app's DLL search path could not be restored after Setup launch.")


class _SetupLease:
    """A cross-process gate without thread-owned mutex abandonment on timeout."""

    def __init__(self, key: str):
        self.handle = None
        if sys.platform != "win32":
            return  # Tests run portably; real apply is exposed only on Windows.
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateSemaphoreW.argtypes = [ctypes.c_void_p, wintypes.LONG, wintypes.LONG, wintypes.LPCWSTR]
        api.CreateSemaphoreW.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.ReleaseSemaphore.argtypes = [wintypes.HANDLE, wintypes.LONG, ctypes.c_void_p]
        api.ReleaseSemaphore.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        name = "Local\\Luma.Update." + hashlib.sha256(key.encode("utf-8")).hexdigest()
        handle = api.CreateSemaphoreW(None, 1, 1, name)
        if not handle:
            raise InstallationError("The installer operation guard could not be created.")
        result = api.WaitForSingleObject(handle, 0)
        if result != 0:
            api.CloseHandle(handle)
            if result == 258:  # WAIT_TIMEOUT: another Luma owns the setup gate.
                raise InstallationPending("Setup is already running for this installation in another Luma window. Your current app stays open.")
            raise InstallationError("The installer operation guard could not be acquired.")
        self.api, self.handle = api, handle

    def close(self):
        handle, self.handle = self.handle, None
        if handle is not None:
            self.api.ReleaseSemaphore(handle, 1, None)
            self.api.CloseHandle(handle)


def _release_finished_setup(key, process):
    with _SETUP_LOCK:
        if _RUNNING_SETUPS.get(key) is process:
            del _RUNNING_SETUPS[key]
            lease = _SETUP_LEASES.pop(key, None)
            if lease is not None:
                lease.close()


def _retain_pending_guard(key, process):
    def finish():
        try:
            process.wait()  # Kernel wait, not an indefinite polling loop.
        except (OSError, subprocess.SubprocessError):
            return  # Never unlock while completion is unknown.
        _release_finished_setup(key, process)
    threading.Thread(target=finish, daemon=True, name="Luma Setup completion").start()


def _plain(path: Path) -> bool:
    return not path.is_symlink() and not path.is_junction()


def _plain_absolute_file(path: Path) -> Path:
    path = Path(path)
    if (not path.is_absolute() or ".." in path.parts or not path.is_file()
            or path.stat().st_nlink != 1
            or not all(_plain(p) for p in (path, *path.parents))):
        raise InstallationError("The application or installer path is missing, linked or unsafe.")
    return path.resolve(strict=True)


def detect_installation(executable: Path | None = None, *, frozen: bool | None = None,
                        platform: str | None = None) -> InstallationContext:
    """Never reinterpret a damaged managed payload as a portable distribution."""
    executable = Path(sys.executable if executable is None else executable)
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    if not frozen:
        return InstallationContext("source", executable)
    if platform != "win32":
        return InstallationContext("unsupported", executable)
    try:
        # Check the lexical layout before resolving: resolving a link could hide
        # that a managed marker was moved or redirected outside its original root.
        marker = executable.parent / MARKER_NAME
        root_hints = (executable.parent / name for name in (
            "launcher-owner.txt", "active-payload.txt", "active-payload.pending.json",
            "managed-uninstall.pending.json", "managed-uninstall.ready"))
        managed_hint = (marker.exists() or marker.is_symlink() or marker.is_junction()
                        or executable.parent.parent.name.casefold() == "versions"
                        or any(path.exists() or path.is_symlink() or path.is_junction() for path in root_hints))
        if managed_hint:
            installed_executables(executable)
            canonical = _plain_absolute_file(executable)
            # The shared installer validator already bounded/read/validated JSON.
            version = canonical.parent.name.rsplit("-", 1)[0]
            return InstallationContext("managed", canonical, canonical.parent.parent.parent, version)
        canonical = _plain_absolute_file(executable)
        if canonical.name.casefold() != "luma.exe":
            raise InstallationError("This frozen executable is not a recognized Luma package.")
        return InstallationContext("portable", canonical)
    except (InstallerError, OSError, ValueError) as error:
        raise InstallationError("The Luma installation marker or path is invalid. Your current app stays open.") from error


def _managed_context(context: InstallationContext) -> InstallationContext:
    if not isinstance(context, InstallationContext) or context.mode != "managed":
        raise InstallationError("Setup updates require a validated managed Luma installation.")
    actual = detect_installation(context.executable, frozen=True, platform="win32")
    if actual != context:
        raise InstallationError("The installation changed while preparing the update. Your current app stays open.")
    return actual


def _check_cancel(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise InstallationCancelled("Update cancelled. Your current Luma is still running.")


def _verify_setup(path: Path, expected_sha256: str, version: str,
                  cancelled: Callable[[], bool]) -> Path:
    if not isinstance(version, str) or not _STABLE_VERSION.fullmatch(version):
        raise InstallationError("This is not a supported stable Windows update version.")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise InstallationError("The verified Setup checksum is missing or invalid.")
    path = _plain_absolute_file(path)
    if path.name != f"Luma-{version}-windows-x64-setup.exe":
        raise InstallationError("The verified Setup filename does not match the selected official release.")
    before = path.stat()
    if not 0 < before.st_size <= MAX_SETUP_BYTES:
        raise InstallationError("The Setup executable exceeds the download safety limit.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise InstallationError("The downloaded Setup is not a Windows executable.")
        stream.seek(0)
        read = 0
        while block := stream.read(min(128 * 1024, before.st_size - read + 1)):
            _check_cancel(cancelled)
            read += len(block)
            if read > before.st_size:
                raise InstallationError("The Setup executable changed size during checksum verification.")
            digest.update(block)
    after = path.stat()
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(after) or digest.hexdigest() != expected_sha256.lower():
        raise InstallationError("SHA-256 verification failed immediately before Setup launch. Your current app stays open.")
    return path


def _updated_payload(context: InstallationContext, version: str) -> ManagedUpdate:
    context = _managed_context(context)
    try:
        matching = [path.resolve(strict=True) for path in installed_executables(context.executable)
                    if path.parent.name.rsplit("-", 1)[0] == version]
    except (InstallerError, OSError) as error:
        raise InstallationError("Setup finished, but the new installed payload could not be verified. Your current app stays open.") from error
    if len(matching) != 1 or matching[0] == context.executable:
        raise InstallationError("Setup did not produce one unambiguous new version in the original installation. Your current app stays open.")
    candidate = matching[0]
    actual = detect_installation(candidate, frozen=True, platform="win32")
    if actual.root != context.root or actual.version != version:
        raise InstallationError("The new payload is outside the original installation. Your current app stays open.")
    try:
        from .managed_launch import read_active_payload
        selected = read_active_payload(context.root)
        if selected.executable != candidate or selected.version != version:
            raise InstallerError("The stable root still selects another payload.")
    except (InstallerError, OSError) as error:
        raise InstallationError("Setup finished, but the stable launch entry does not select the verified new payload. Your current app stays open.") from error
    return ManagedUpdate(version, candidate.parent, candidate, context.executable, context.root)


def apply_verified_setup(setup_path: Path, expected_sha256: str, version: str,
                         installation: InstallationContext, *,
                         cancelled: Callable[[], bool] = lambda: False,
                         status: Callable[[str], None] = lambda text: None,
                         timeout: float = SETUP_TIMEOUT, popen=None) -> ManagedUpdate:
    """Wait for Setup exit 0, then locate its validated side-by-side payload.

    The caller obtains the Setup and checksum only from the official release.
    This helper verifies the digest again immediately before launch. /SILENT
    displays native progress and error/cancel UI while fixing /DIR and skipping
    the finish-page launch, preventing duplicate or unverified app launches.

    Cancellation after launch does not kill Setup mid-transaction. A timeout
    raises InstallationPending and retains the process and cross-process gate;
    leave the cached Setup file in place and let the native installer finish.
    None of these failure paths quits the running viewer or claims rollback of
    installer-owned shortcuts/registration after an incomplete observation.
    """
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= SETUP_TIMEOUT:
        raise InstallationError("The installer wait deadline is invalid.")
    _check_cancel(cancelled)
    context = _managed_context(installation)
    if not isinstance(version, str) or not _STABLE_VERSION.fullmatch(version):
        raise InstallationError("This is not a supported stable Windows update version.")
    if tuple(map(int, version.split("."))) <= tuple(map(int, context.version.split("."))):
        raise InstallationError("Setup must install a newer version than the running managed payload.")
    setup = _verify_setup(Path(setup_path), expected_sha256, version, cancelled)
    _check_cancel(cancelled)
    # A successful previous installer can leave multiple old payloads. Never
    # delete any of them: Explorer can still have their DLLs mapped.
    key = os.path.normcase(str(context.root))
    with _SETUP_LOCK:
        previous = _RUNNING_SETUPS.get(key)
        if previous is not None and previous.poll() is None:
            raise InstallationPending("Setup is still running for this installation. Finish or cancel its window before trying again. Your current Luma stays open.")
        _RUNNING_SETUPS.pop(key, None)
        previous_lease = _SETUP_LEASES.pop(key, None)
        if previous_lease is not None:
            previous_lease.close()
        context = _managed_context(context)
        command = [str(setup), "/SP-", "/SILENT", "/NORESTART", "/RESTARTEXITCODE=11",
                   "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", f"/DIR={context.root}"]
        environment = os.environ.copy()
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        _check_cancel(cancelled)
        lease = _SetupLease(key)
        process = None
        try:
            with _setup_dll_search_path():
                process = (popen or subprocess.Popen)(command, cwd=str(setup.parent), env=environment,
                                                      shell=False, close_fds=True)
                _RUNNING_SETUPS[key] = process
                _SETUP_LEASES[key] = lease
        except Exception as error:
            if process is None:
                lease.close()
                raise InstallationError("The verified Setup could not be started safely. Your current Luma stays open.") from error
            # Restoration failed after the process was created. Keep its guard
            # and never launch another installer or report success/rollback.
            _retain_pending_guard(key, process)
            raise InstallationPending("Setup started, but the original DLL search path could not be restored. Your current Luma stays open; finish or cancel Setup before retrying.") from error
    status("Setup is running in its own window. You can cancel there; your current Luma stays open.")
    deadline = time.monotonic() + timeout
    was_cancelled = False
    while True:
        was_cancelled = was_cancelled or cancelled()
        code = process.poll()
        if code is not None:
            _release_finished_setup(key, process)
            break
        if time.monotonic() >= deadline:
            _retain_pending_guard(key, process)
            raise InstallationPending("Setup has not finished within the waiting period and may still be running. Finish or cancel its window before retrying. Your current Luma stays open.")
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    if was_cancelled or code in (2, 5):
        raise InstallationCancelled("Update cancelled. Your current Luma stays open; any retained installer payloads are safe to keep.")
    if code != 0:
        raise InstallationError(f"Setup did not complete successfully (exit code {code}). Your current Luma stays open; the previous payload is retained.")
    _check_cancel(cancelled)
    return _updated_payload(context, version)
