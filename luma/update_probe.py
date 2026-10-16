"""One-shot, bounded, loopback-only confirmation of a newly opened Luma.

The child reports its embedded version, executable path and actual process ID.
The parent never trusts a release label, marker or successful Popen alone. This
is startup confirmation, not publisher authentication; release checksums and
managed installer ownership are validated separately before this step.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from typing import Callable

PROTOCOL = 1
MAX_ACK_BYTES = 4096
STARTUP_TIMEOUT = 30
_NONCE = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")


class LaunchError(RuntimeError):
    pass


class LaunchCancelled(LaunchError):
    pass


@dataclass(frozen=True)
class LaunchAcknowledgment:
    version: str
    executable: Path
    pid: int
    os_executable: Path | None = None
    launcher_pid: int | None = None
    launcher_executable: Path | None = None


def process_executable(process) -> Path | None:
    """Query the created process handle independently of its own acknowledgment."""
    if sys.platform != "win32":
        return None  # Portable protocol tests; the distributed updater is Windows-only.
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.QueryFullProcessImageNameW.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(buffer))
    handle = getattr(process, "_handle", None)
    if handle is None or not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
        raise LaunchError("Windows could not verify the new process executable. Your current Luma stays open.")
    try:
        return _canonical_executable(Path(buffer.value))
    except OSError as error:
        raise LaunchError("Windows reported an invalid new process executable. Your current Luma stays open.") from error


def _process_creation_time(api, handle) -> int:
    import ctypes
    from ctypes import wintypes
    creation, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not api.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exited),
                               ctypes.byref(kernel), ctypes.byref(user)):
        raise LaunchError("Windows could not verify the new process creation time. Your current Luma stays open.")
    return (creation.dwHighDateTime << 32) | creation.dwLowDateTime


def _process_parent_pid(api, pid: int) -> int:
    import ctypes
    from ctypes import wintypes
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    api.Process32FirstW.restype = wintypes.BOOL
    api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    api.Process32NextW.restype = wintypes.BOOL
    snapshot = api.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS.
    if snapshot in (None, ctypes.c_void_p(-1).value):
        raise LaunchError("Windows could not inspect the launcher-child relationship. Your current Luma stays open.")
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        present = api.Process32FirstW(snapshot, ctypes.byref(entry))
        for _ in range(65536):
            if not present:
                break
            if entry.th32ProcessID == pid:
                return int(entry.th32ParentProcessID)
            present = api.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        api.CloseHandle(snapshot)
    raise LaunchError("Windows did not find the acknowledged child process. Your current Luma stays open.")


def _verified_launcher_child(launcher_process, child_pid: int, expected_executable: Path) -> Path:
    """Accept only a live direct child of the exact still-live created launcher.

    The launcher's retained Popen handle remains alive throughout verification,
    preventing an exited-parent PID from being mistaken for a later process.
    The child is opened by PID once and every subsequent identity/liveness query
    uses that retained handle. Neither a self-reported path nor a PID alone is
    accepted as evidence of a successful stable-entry restart.
    """
    if sys.platform != "win32" or launcher_process.poll() is not None:
        raise LaunchError("The stable launcher is not running during child verification. Your current Luma stays open.")
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
    api.GetProcessTimes.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.OpenProcess(0x1000 | 0x100000, False, child_pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE.
    if not handle:
        raise LaunchError("Windows could not open the acknowledged payload process. Your current Luma stays open.")
    try:
        if api.WaitForSingleObject(handle, 0) != 258:  # WAIT_TIMEOUT means still running.
            raise LaunchError("The new payload exited before it could be verified. Your current Luma stays open.")
        actual = process_executable(SimpleNamespace(_handle=handle))
        if actual is None or os.path.normcase(str(actual)) != os.path.normcase(str(expected_executable)):
            raise LaunchError("The Windows child process image does not match the selected payload. Your current Luma stays open.")
        if _process_parent_pid(api, child_pid) != launcher_process.pid:
            raise LaunchError("The acknowledged payload is not the created launcher's direct child. Your current Luma stays open.")
        launcher_handle = getattr(launcher_process, "_handle", None)
        if launcher_handle is None:
            raise LaunchError("The stable launcher process handle is unavailable. Your current Luma stays open.")
        if _process_creation_time(api, handle) < _process_creation_time(api, launcher_handle):
            raise LaunchError("The acknowledged payload predates this launcher. Your current Luma stays open.")
        if launcher_process.poll() is not None or api.WaitForSingleObject(handle, 0) != 258:
            raise LaunchError("The stable launcher or its payload exited during verification. Your current Luma stays open.")
        return actual
    finally:
        api.CloseHandle(handle)


def _validate_launcher_target(launcher: Path, executable: Path) -> Path:
    """The pointer owner supplies the strict reader; no fallback target exists."""
    launcher = _canonical_executable(launcher)
    if (executable.parent.parent.name != "versions"
            or launcher.parent != executable.parent.parent.parent):
        raise LaunchError("The stable entry and payload do not belong to the same managed root.")
    # Registration-helper integration owns this reader. Keep its exact pointer
    # contract centralized rather than maintaining two diverging parsers here.
    from .managed_launch import read_active_payload
    from .installer import InstallerError
    try:
        selected = read_active_payload(launcher.parent)
    except (InstallerError, OSError) as error:
        raise LaunchError("The stable entry ownership or active pointer could not be verified. Your current Luma stays open.") from error
    if selected.executable != executable:
        raise LaunchError("The active managed payload changed before relaunch. Your current Luma stays open.")
    return launcher


def validate_startup_endpoint(port: int, nonce: str) -> tuple[int, str]:
    """No user-provided host, URL, output path or non-loopback endpoint exists."""
    if type(port) is not int or not 1024 <= port <= 65535:
        raise LaunchError("The update startup port is invalid.")
    if not isinstance(nonce, str) or not _NONCE.fullmatch(nonce):
        raise LaunchError("The update startup token is invalid.")
    return port, nonce


def _canonical_executable(executable: Path) -> Path:
    executable = Path(executable)
    if (not executable.is_absolute() or ".." in executable.parts
            or executable.name.casefold() != "luma.exe" or not executable.is_file()
            or any(path.is_symlink() or path.is_junction()
                   for path in (executable, *executable.parents))):
        raise LaunchError("The new application path is missing, linked or unsafe. Your current Luma stays open.")
    return executable.resolve(strict=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate startup field")
        result[key] = value
    return result


def _validated_ack(data: bytes, nonce: str, expected_version: str,
                   executable: Path, expected_pid: int) -> LaunchAcknowledgment:
    try:
        if not data or len(data) > MAX_ACK_BYTES:
            raise ValueError("startup message size")
        payload = json.loads(data, object_pairs_hook=_unique_object)
        if (not isinstance(payload, dict)
                or set(payload) != {"protocol", "nonce", "version", "executable", "pid", "frozen"}
                or type(payload["protocol"]) is not int or payload["protocol"] != PROTOCOL
                or payload["nonce"] != nonce or payload["version"] != expected_version
                or payload["frozen"] is not True or type(payload["pid"]) is not int
                or payload["pid"] <= 0 or (expected_pid is not None and payload["pid"] != expected_pid)
                or not isinstance(payload["executable"], str)):
            raise ValueError("startup identity mismatch")
        reported = Path(payload["executable"])
        if (not reported.is_absolute()
                or os.path.normcase(os.path.normpath(str(reported))) != os.path.normcase(str(executable))):
            raise ValueError("startup executable mismatch")
        actual = _canonical_executable(reported)
        if os.path.normcase(str(actual)) != os.path.normcase(str(executable)):
            raise ValueError("startup executable mismatch")
    except (ValueError, TypeError, UnicodeError, RecursionError, OSError) as error:
        raise LaunchError("The new process did not confirm the expected embedded version, executable path and process ID. Your current Luma stays open.") from error
    return LaunchAcknowledgment(expected_version, actual, payload["pid"])


def _check_cancel(cancelled):
    if cancelled():
        raise LaunchCancelled("Launch confirmation cancelled. Your current Luma stays open.")


def launch_verified_update(executable: Path, expected_version: str, *,
                           cancelled: Callable[[], bool] = lambda: False,
                           timeout: float = STARTUP_TIMEOUT, popen=None,
                           launcher: Path | None = None) -> LaunchAcknowledgment:
    """Call from a worker thread; return only after the actual GUI acknowledges.

    The listener binds only 127.0.0.1 and closes on every success/error/cancel path.
    An unresponsive or legacy process never authorizes quitting the current app.
    A failed probe leaves the current app and both versioned payloads intact.
    """
    if (not isinstance(expected_version, str) or not _VERSION.fullmatch(expected_version)
            or not isinstance(timeout, (int, float)) or not 0 < timeout <= STARTUP_TIMEOUT):
        raise LaunchError("The update startup verification request is invalid.")
    try:
        executable = _canonical_executable(executable)
    except OSError as error:
        raise LaunchError("The new application could not be inspected. Your current Luma stays open.") from error
    _check_cancel(cancelled)
    entry = _validate_launcher_target(Path(launcher), executable) if launcher is not None else executable
    nonce = secrets.token_hex(32)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        # Exclusive binding avoids a second local process sharing the endpoint on
        # Windows. No SO_REUSEADDR and no externally reachable listener is used.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        validate_startup_endpoint(port, nonce)
        command = [str(entry), "--update-startup-port", str(port),
                   "--update-startup-nonce", nonce]
        environment = os.environ.copy()
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        try:
            process = (popen or subprocess.Popen)(command, cwd=str(entry.parent), env=environment,
                                                  shell=False, close_fds=True)
        except OSError as error:
            raise LaunchError("The new application could not be launched. Your current Luma stays open.") from error
        if launcher is not None:
            root_image = process_executable(process)
            if root_image is None or os.path.normcase(str(root_image)) != os.path.normcase(str(entry)):
                raise LaunchError("Windows did not confirm the expected stable entry process. Your current Luma stays open.")
        deadline = time.monotonic() + timeout
        while True:
            _check_cancel(cancelled)
            if process.poll() is not None:
                raise LaunchError("The new application exited before confirming startup. Your current Luma stays open.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LaunchError("The new application did not confirm startup in time. Your current Luma stays open.")
            listener.settimeout(min(remaining, 0.1))
            try:
                connection, address = listener.accept()
            except socket.timeout:
                continue
            with connection:
                if address[0] != "127.0.0.1":
                    raise LaunchError("An invalid update startup connection was rejected.")
                received = bytearray()
                while True:
                    _check_cancel(cancelled)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise LaunchError("The new application's startup message timed out. Your current Luma stays open.")
                    connection.settimeout(min(remaining, 0.1))
                    try:
                        block = connection.recv(min(1024, MAX_ACK_BYTES + 1 - len(received)))
                    except socket.timeout:
                        if process.poll() is not None:
                            raise LaunchError("The new application exited during startup confirmation. Your current Luma stays open.")
                        continue
                    if not block:
                        break
                    received.extend(block)
                    if len(received) > MAX_ACK_BYTES:
                        raise LaunchError("The new application's startup message exceeded the safety limit.")
                acknowledgment = _validated_ack(bytes(received), nonce, expected_version, executable,
                                                 None if launcher is not None else process.pid)
                if launcher is not None:
                    os_executable = _verified_launcher_child(process, acknowledgment.pid, executable)
                    _validate_launcher_target(entry, executable)  # Fail if the committed pointer drifted.
                else:
                    os_executable = process_executable(process)
                    if os_executable is not None and os.path.normcase(str(os_executable)) != os.path.normcase(str(executable)):
                        raise LaunchError("The Windows process image does not match the new application. Your current Luma stays open.")
                _check_cancel(cancelled)
                if process.poll() is not None:
                    raise LaunchError("The new application exited during startup confirmation. Your current Luma stays open.")
                return LaunchAcknowledgment(acknowledgment.version, acknowledgment.executable,
                                            acknowledgment.pid, os_executable,
                                            process.pid if launcher is not None else None,
                                            entry if launcher is not None else None)


def _send_startup_ack(port: int, nonce: str) -> bool:
    """Derive identity from this running packaged app, never command-line claims."""
    from . import __version__
    if not getattr(sys, "frozen", False):
        return False
    try:
        port, nonce = validate_startup_endpoint(port, nonce)
        executable = _canonical_executable(Path(sys.executable))
        payload = json.dumps({"protocol": PROTOCOL, "nonce": nonce, "version": __version__,
                              "executable": str(executable), "pid": os.getpid(), "frozen": True},
                             separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_ACK_BYTES:
            return False
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as connection:
            connection.sendall(payload)
            connection.shutdown(socket.SHUT_WR)
        return True
    except (LaunchError, OSError, ValueError):
        return False


def acknowledge_startup(port: int, nonce: str) -> threading.Thread:
    """Call only after the new main window reaches its first event-loop turn.

    Connect/send has a one-second bound and happens off the GUI thread. A bad or
    stale endpoint cannot block normal startup or open a persistent connection.
    """
    validate_startup_endpoint(port, nonce)
    thread = threading.Thread(target=_send_startup_ack, args=(port, nonce), daemon=True,
                              name="Luma startup acknowledgment")
    thread.start()
    return thread
