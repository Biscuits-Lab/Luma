"""Strict stable-entry selection and crash-recoverable managed activation.

Only Setup/confirmed repair may publish a pointer. Registration remains the
existing bounded current-user transaction; this module adds a durable same-root
participant. Recovery compares exact pointer bytes, receipt and registry values
before doing anything, and never repairs a changed foreign state by guessing.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from . import installer, shell_integration as shell

POINTER_NAME = "active-payload.txt"
OWNER_NAME = "launcher-owner.txt"
JOURNAL_NAME = "active-payload.pending.json"
UNINSTALL_NAME = "managed-uninstall.pending.json"
READY_NAME = "managed-uninstall.ready"
MAX_JOURNAL = 8 * 1024 * 1024
MAX_BINARY = 512 * 1024 * 1024
_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
_HEX = r"[0-9a-f]{64}"
_LOCK = threading.Lock()
_ROOT_LOCKS: dict[str, threading.Lock] = {}


class ManagedLaunchError(installer.InstallerError):
    pass


@dataclass(frozen=True)
class ActivePayload:
    root: Path
    executable: Path
    version: str
    payload_id: str
    bundle_sha256: str
    executable_sha256: str

    @property
    def launcher(self) -> Path:
        return self.root / "Luma.exe"

    def pointer(self) -> bytes:
        return (f"LUMA-ACTIVE-1\n{self.payload_id}\n{self.bundle_sha256}\n"
                f"{self.executable_sha256}\n").encode("ascii")


def _plain(path: Path, *, directory=False) -> os.stat_result:
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or (directory and not stat.S_ISDIR(info.st_mode))
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
        raise ManagedLaunchError("Managed paths must be ordinary files/directories, without links or reparse points.")
    return info


def _root_path(root: Path) -> Path:
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise ManagedLaunchError("The managed root must be an absolute local path.")
    if os.name == "nt" and (not re.fullmatch(r"[A-Za-z]:", root.drive) or root.root != "\\"):
        raise ManagedLaunchError("The managed root must be on a local drive.")
    for ancestor in (root, *root.parents):
        _plain(ancestor, directory=True)
    return root.resolve(strict=True)


@contextmanager
def _pinned_root(root: Path):
    root = _root_path(root)
    handles = []
    api = None
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            api.CreateFileW.restype = wintypes.HANDLE
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.CloseHandle.restype = wintypes.BOOL
            for ancestor in reversed((root, *root.parents)):
                # Deny directory rename/delete while any protocol file is used.
                handle = api.CreateFileW(str(ancestor), 0x80, 3, None, 3, 0x02200000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise ManagedLaunchError("Could not pin the managed installation directories.")
                handles.append(handle)
                _plain(ancestor, directory=True)
        yield root
    finally:
        if api is not None:
            for handle in reversed(handles):
                api.CloseHandle(handle)


@contextmanager
def _open_file(path: Path):
    before = _plain(path)
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        api.CreateFileW.restype = wintypes.HANDLE
        handle = api.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ManagedLaunchError("Could not read the owned managed file exclusively against mutation.")
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.CloseHandle(handle)
            raise
    else:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or getattr(opened, "st_file_attributes", 0) & 0x400
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
            raise ManagedLaunchError("A managed file changed while being opened.")
        yield stream, opened
        after = os.fstat(stream.fileno())
        path_after = _plain(path)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(opened) != identity(after) or identity(after) != identity(path_after):
            raise ManagedLaunchError("A managed file changed while being verified.")


def _read(path: Path, limit: int, *, missing=False) -> bytes | None:
    try:
        with _open_file(path) as (stream, info):
            if info.st_size > limit:
                raise ManagedLaunchError("A managed protocol file exceeds its bound.")
            result = stream.read(limit + 1)
            if len(result) > limit:
                raise ManagedLaunchError("A managed protocol file exceeds its bound.")
            return result
    except FileNotFoundError:
        if missing:
            return None
        raise ManagedLaunchError("A required managed protocol file is missing.") from None


def _digest(path: Path) -> str:
    with _open_file(path) as (stream, info):
        if info.st_size < 2 or info.st_size > MAX_BINARY:
            raise ManagedLaunchError("The managed executable has an invalid size.")
        digest = hashlib.sha256()
        count = 0
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_BINARY:
                raise ManagedLaunchError("The managed executable exceeds its bound.")
            digest.update(chunk)
        return digest.hexdigest()


def _owner(root: Path) -> bytes:
    raw = _read(root / OWNER_NAME, 128)
    if not re.fullmatch(rb"LUMA-LAUNCHER-1\n[0-9a-f]{64}\n", raw):
        raise ManagedLaunchError("The root launcher ownership record is invalid.")
    if _digest(root / "Luma.exe") != raw.decode("ascii").splitlines()[1]:
        raise ManagedLaunchError("The root launcher does not match its ownership record.")
    return raw


def _payload(root: Path, payload_id: str) -> ActivePayload:
    match = re.fullmatch(f"({_VERSION})-([0-9a-f]{{24}})", payload_id)
    if match is None:
        raise ManagedLaunchError("The active payload ID is invalid.")
    directory = root / "versions" / payload_id
    with _pinned_root(directory):
        raw = _read(directory / installer.MARKER_NAME, 4096)
        # Match the native parser: plain ASCII JSON strings, no escapes or BOM.
        if b"\\" in raw or any(byte > 127 for byte in raw):
            raise ManagedLaunchError("The payload marker is not plain ASCII metadata.")
        try:
            data = json.loads(raw, object_pairs_hook=shell._no_duplicate_members,
                              parse_constant=shell._reject_constant)
        except (ValueError, UnicodeError, RecursionError, shell.ShellIntegrationError) as error:
            raise ManagedLaunchError("The payload marker is invalid.") from error
        if (not isinstance(data, dict) or set(data) != {
                "managed_install", "layout", "version", "payload_id", "bundle_sha256"}
                or data.get("managed_install") is not True or data.get("layout") != "versions-v1"
                or data.get("version") != match[1] or data.get("payload_id") != payload_id
                or not isinstance(data.get("bundle_sha256"), str)
                or not re.fullmatch(_HEX, data["bundle_sha256"])
                or data["bundle_sha256"][:24] != match[2]):
            raise ManagedLaunchError("The payload marker does not match the selected version.")
        exe = directory / "Luma.exe"
        return ActivePayload(root, exe, match[1], payload_id, data["bundle_sha256"], _digest(exe))


def _selected(root: Path, raw: bytes) -> ActivePayload:
    try:
        lines = raw.decode("ascii").split("\n")
    except UnicodeError as error:
        raise ManagedLaunchError("The active pointer is not ASCII.") from error
    if (len(raw) > 512 or len(lines) != 5 or lines[0] != "LUMA-ACTIVE-1" or lines[4] != ""
            or not re.fullmatch(_HEX, lines[2]) or not re.fullmatch(_HEX, lines[3])):
        raise ManagedLaunchError("The active pointer is invalid.")
    selected = _payload(root, lines[1])
    if selected.pointer() != raw:
        raise ManagedLaunchError("The active pointer does not match the installed payload bytes.")
    return selected


def read_active_payload(root: Path) -> ActivePayload:
    """Read one exact pointer; never search siblings or fall back to an old app."""
    with _pinned_root(root) as root:
        _owner(root)
        return _selected(root, _read(root / POINTER_NAME, 512))


def _mutex_name(root: Path) -> str:
    return "Local\\LumaManagedRoot-" + hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()


def _ready_bytes(root: Path, owner: bytes, pointer: bytes | None, uninstall: bytes) -> bytes:
    pointer_hash = "-" if pointer is None else hashlib.sha256(pointer).hexdigest()
    return ("LUMA-UNINSTALL-1\n" + owner.decode("ascii").splitlines()[1] + "\n"
            + hashlib.sha256(owner).hexdigest() + "\n" + pointer_hash + "\n"
            + hashlib.sha256(uninstall).hexdigest() + "\n" + _mutex_name(root) + "\n").encode("ascii")


@contextmanager
def _root_guard(root: Path):
    with _pinned_root(root) as root:
        key = str(root).casefold()
        with _LOCK:
            lock = _ROOT_LOCKS.setdefault(key, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ManagedLaunchError("Another operation is changing this installation.")
        handle = None
        api = None
        acquired = False
        try:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                api = ctypes.WinDLL("kernel32", use_last_error=True)
                api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
                api.CreateMutexW.restype = wintypes.HANDLE
                api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                api.WaitForSingleObject.restype = wintypes.DWORD
                api.ReleaseMutex.argtypes = [wintypes.HANDLE]
                api.CloseHandle.argtypes = [wintypes.HANDLE]
                name = _mutex_name(root)
                handle = api.CreateMutexW(None, False, name)
                if not handle or api.WaitForSingleObject(handle, 0) not in (0, 0x80):
                    raise ManagedLaunchError("Another process is changing this installation.")
                acquired = True
            yield root
        finally:
            if handle:
                if acquired:
                    api.ReleaseMutex(handle)
                api.CloseHandle(handle)
            lock.release()


def _sync_directory(root: Path) -> None:
    if os.name != "nt":
        fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _replace(source: Path, target: Path) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        api.MoveFileExW.restype = wintypes.BOOL
        if not api.MoveFileExW(str(source), str(target), 0x1 | 0x8):
            raise OSError(ctypes.get_last_error(), "Could not durably replace the owned pointer")
    else:
        os.replace(source, target)
        _sync_directory(target.parent)


def _cas_file(path: Path, expected: bytes | None, replacement: bytes | None, limit=MAX_JOURNAL) -> None:
    if _read(path, limit, missing=True) != expected:
        raise ManagedLaunchError("Managed state changed; it was preserved.")
    if expected == replacement:
        return
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        if replacement is None:
            # Atomic rename makes deletion durable, without truncating the file.
            _replace(path, temp)
            temp.unlink()
            _sync_directory(path.parent)
            return
        with temp.open("xb") as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if _read(path, limit, missing=True) != expected:
            raise ManagedLaunchError("Managed state changed before commit; it was preserved.")
        _replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw, object_pairs_hook=shell._no_duplicate_members,
                           parse_constant=shell._reject_constant)
    except (ValueError, UnicodeError, RecursionError, shell.ShellIntegrationError) as error:
        raise ManagedLaunchError("The managed recovery record is invalid.") from error
    if not isinstance(value, dict):
        raise ManagedLaunchError("The managed recovery record is not an object.")
    return value


def _encoded(document: dict) -> bytes:
    result = json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(result) > MAX_JOURNAL:
        raise ManagedLaunchError("The managed recovery record exceeds its bound.")
    return result


def _registry_path(path: Path) -> str:
    """Filesystem-to-registry boundary, replaced only by cross-platform tests."""
    return str(path)


def _check_registry(registry, journal: dict, *, target=False, transitional=False) -> None:
    before, after = shell._entry_map(journal["base"]), shell._entry_map(journal["target"])
    changes = {shell._address(change): change for change in journal["changes"]}
    for address in before.keys() | after.keys() | changes.keys():
        change = changes.get(address)
        expected = after.get(address) if target else before.get(address)
        if not target and change is not None:
            expected = change["before"]  # Includes an explicitly adopted Open With command.
        actual = shell._encoded_value(registry.read(*address))
        if transitional and change is not None and actual in (change["before"], change["after"]):
            continue
        if actual != expected:
            raise ManagedLaunchError("An externally changed registry value prevents managed recovery.")


def _journal(root: Path, raw: bytes) -> dict:
    data = _json(raw)
    if set(data) != {"version", "owner", "before", "after", "registry_before", "registry_pending", "uninstall_before", "ready_before"} or type(data["version"]) is not int or data["version"] != 1:
        raise ManagedLaunchError("Unknown managed activation journal.")
    if data["owner"] != _owner(root).decode("ascii"):
        raise ManagedLaunchError("The journal belongs to a different root launcher.")
    for name in ("before", "after"):
        value = data[name]
        if value is None and name == "before":
            continue
        if not isinstance(value, str):
            raise ManagedLaunchError("Invalid saved pointer.")
        try:
            encoded = value.encode("ascii")
        except UnicodeError as error:
            raise ManagedLaunchError("Invalid saved pointer encoding.") from error
        _selected(root, encoded)
    uninstall = data["uninstall_before"]
    if uninstall is not None:
        if not isinstance(uninstall, str):
            raise ManagedLaunchError("Invalid saved uninstall receipt.")
        saved = _uninstall_receipt(root, uninstall.encode("ascii"), check_pointer=False)
        if saved["state"] != "prepared" or saved["pointer"] != data["before"]:
            raise ManagedLaunchError("Only an unchanged prepared uninstall can be cancelled by activation.")
    ready = data["ready_before"]
    if ready is not None:
        before = None if data["before"] is None else data["before"].encode("ascii")
        if (uninstall is None or not isinstance(ready, str)
                or ready != _ready_bytes(root, data["owner"].encode("ascii"), before,
                                         uninstall.encode("ascii")).decode("ascii")):
            raise ManagedLaunchError("The saved final-uninstall receipt is inconsistent.")
    pending = data["registry_pending"]
    shell._validate_document(pending)
    if (pending["state"] != "pending" or pending["target"] is None
            or pending.get("managed_pointer_transaction") is not True):
        raise ManagedLaunchError("The activation journal has no registration target.")
    selected = _selected(root, data["after"].encode("ascii"))
    if pending["target"].get("managed_payload") != _registry_path(selected.executable):
        raise ManagedLaunchError("The recovery receipt does not own the selected payload.")
    previous = data["registry_before"]
    if previous is None:
        if pending["base"] is not None:
            raise ManagedLaunchError("The saved registry receipt is missing.")
    else:
        if (not isinstance(previous, dict) or set(previous) != {"type", "data"}
                or type(previous["type"]) is not int or previous["type"] != shell.REG_SZ
                or not isinstance(previous["data"], str)
                or len(previous["data"].encode("utf-8")) > shell.MAX_MANIFEST_BYTES):
            raise ManagedLaunchError("The saved registry receipt is invalid.")
        document = _json(previous["data"].encode("utf-8"))
        shell._validate_document(document)
        if document != shell._active(pending["base"]):
            raise ManagedLaunchError("The saved registry receipt is inconsistent.")
    return data


def _recover_locked(root: Path, registry) -> bool:
    path = root / JOURNAL_NAME
    raw = _read(path, MAX_JOURNAL, missing=True)
    if raw is None:
        return False
    data = _journal(root, raw)
    before = None if data["before"] is None else data["before"].encode("ascii")
    after = data["after"].encode("ascii")
    pointer = _read(root / POINTER_NAME, 512, missing=True)
    if pointer not in (before, after):
        raise ManagedLaunchError("The active pointer changed outside the interrupted operation.")
    pending = data["registry_pending"]
    receipt = registry.read(shell.METADATA_KEY, shell.MANIFEST_NAME)
    base_receipt = shell._decoded_value(data["registry_before"])
    pending_receipt = shell._manifest_value(pending)
    target_receipt = shell._manifest_value(shell._active(pending["target"]))
    uninstall_before = None if data["uninstall_before"] is None else data["uninstall_before"].encode("ascii")
    uninstall_current = _read(root / UNINSTALL_NAME, MAX_JOURNAL, missing=True)
    committed = receipt == target_receipt and pointer == after
    ready_before = None if data["ready_before"] is None else data["ready_before"].encode("ascii")
    ready_current = _read(root / READY_NAME, 512, missing=True)
    if ready_current != ready_before and not (committed and ready_current is None):
        raise ManagedLaunchError("The final-uninstall receipt changed outside the interrupted activation.")
    if uninstall_current != uninstall_before and not (committed and uninstall_current is None):
        raise ManagedLaunchError("The uninstall receipt changed outside the interrupted activation.")
    if committed:
        _check_registry(registry, pending, target=True)
        _cas_file(root / READY_NAME, ready_current, None, 512)
        _cas_file(root / UNINSTALL_NAME, uninstall_current, None)
    elif receipt == pending_receipt:
        _check_registry(registry, pending, transitional=True)
        shell._rollback(registry, pending, (pending_receipt,), restore_manifest=base_receipt)
        _check_registry(registry, pending)
        _cas_file(root / POINTER_NAME, pointer, before, 512)
    elif receipt == base_receipt:
        _check_registry(registry, pending)
        _cas_file(root / POINTER_NAME, pointer, before, 512)
    else:
        raise ManagedLaunchError("Registry ownership changed; managed recovery was stopped.")
    _cas_file(path, raw, None)
    return True


def recover_managed_activation(root: Path, registry=None) -> bool:
    registry = registry if registry is not None else shell.WinRegistry()
    with _root_guard(root) as root, registry.locked():
        _owner(root)
        return _recover_locked(root, registry)


class _Activation:
    def __init__(self, root: Path, selected: ActivePayload):
        self.root, self.selected = root, selected
        self.raw = None

    def prepare(self, registry, pending, previous):
        root = self.root
        before = _read(root / POINTER_NAME, 512, missing=True)
        if before is not None:
            _selected(root, before)
        data = {"version": 1, "owner": _owner(root).decode("ascii"),
                "before": None if before is None else before.decode("ascii"),
                "after": self.selected.pointer().decode("ascii"),
                "registry_before": shell._encoded_value(previous), "registry_pending": pending,
                "uninstall_before": None, "ready_before": None}
        ready = _read(root / READY_NAME, 512, missing=True)
        if ready is not None:
            data["ready_before"] = ready.decode("ascii")
        uninstall = _read(root / UNINSTALL_NAME, MAX_JOURNAL, missing=True)
        if uninstall is not None:
            data["uninstall_before"] = uninstall.decode("ascii")
        self.raw = _encoded(data)
        _journal(root, self.raw)
        _cas_file(root / JOURNAL_NAME, None, self.raw)

    def activate(self, registry):
        data = _journal(self.root, self.raw)
        _check_registry(registry, data["registry_pending"], target=True)
        before = None if data["before"] is None else data["before"].encode("ascii")
        _cas_file(self.root / POINTER_NAME, before, data["after"].encode("ascii"), 512)

    def rollback(self, registry):
        _recover_locked(self.root, registry)

    def finish(self, registry):
        try:
            _recover_locked(self.root, registry)
        except Exception as error:
            raise shell.RecoveryRequired("Activation completed, but its recovery receipt could not be finalized.") from error


def activate_managed_installation(executable: Path, registry=None, *,
                                  allowed_previous_executables=None,
                                  expected_registration=None,
                                  expected_active_pointer: bytes | None = None) -> shell.Result:
    candidates = installer.installed_executables(executable)
    executable = Path(executable).resolve(strict=True)
    root = executable.parent.parent.parent
    registry = registry if registry is not None else shell.WinRegistry()
    with _root_guard(root) as root, registry.locked():
        _owner(root)
        if expected_active_pointer is not None:
            if (not isinstance(expected_active_pointer, bytes) or len(expected_active_pointer) > 512
                    or _read(root / POINTER_NAME, 512, missing=True) != expected_active_pointer):
                raise shell.RegistrationConflict("The selected managed payload changed before startup repair.")
        # An exact confirmed owner may not be silently changed by recovery.
        if expected_registration is not None and _read(root / JOURNAL_NAME, MAX_JOURNAL, missing=True) is not None:
            raise shell.RecoveryRequired("Recover the interrupted managed activation before confirming a migration.")
        recovered = _recover_locked(root, registry)
        if (expected_active_pointer is not None
                and _read(root / POINTER_NAME, 512, missing=True) != expected_active_pointer):
            raise shell.RegistrationConflict("Recovery selected another managed payload; startup repair was stopped.")
        selected = _payload(root, executable.parent.name)
        pending_uninstall = _read(root / UNINSTALL_NAME, MAX_JOURNAL, missing=True)
        if pending_uninstall is not None:
            receipt = _uninstall_receipt(root, pending_uninstall)
            if receipt["state"] != "prepared":
                raise ManagedLaunchError("An uninstall is finalizing; finish it before activating another payload.")
        ready = _read(root / READY_NAME, 512, missing=True)
        if ready is not None:
            pointer = _read(root / POINTER_NAME, 512, missing=True)
            if pending_uninstall is None or ready != _ready_bytes(root, _owner(root), pointer, pending_uninstall):
                raise ManagedLaunchError("The final-uninstall receipt changed; activation was refused.")
        allowed = (tuple(path.resolve(strict=True) for path in candidates)
                   if allowed_previous_executables is None else tuple(allowed_previous_executables))
        permitted = {_registry_path(path.resolve(strict=True)).casefold() for path in candidates}
        if expected_registration is not None:
            if not isinstance(expected_registration, shell.OwnedRegistration):
                raise shell.RegistrationConflict("Managed migration requires the exact confirmed owner.")
            permitted.add(expected_registration.executable.casefold())
        if any(_registry_path(Path(path)).casefold() not in permitted for path in allowed):
            raise shell.RegistrationConflict("Automatic managed activation cannot allow an unrelated owner.")
        result = shell.install(executable, registry=registry,
                               allowed_previous_executables=tuple(_registry_path(Path(path)) for path in allowed),
                               expected_registration=expected_registration,
                               launch_executable=Path(_registry_path(root / "Luma.exe")), _participant=_Activation(root, selected))
        return shell.Result(result.changed_values, result.preserved_values, result.recovered or recovered)


def _uninstall_receipt(root: Path, raw: bytes, *, check_pointer=True) -> dict:
    data = _json(raw)
    if (set(data) != {"version", "owner", "pointer", "state"} or type(data["version"]) is not int or data["version"] != 1
            or data["state"] != "prepared"):
        raise ManagedLaunchError("Unknown managed uninstall receipt.")
    if data["owner"] != _owner(root).decode("ascii"):
        raise ManagedLaunchError("The root launcher changed during uninstall.")
    if data["pointer"] is not None:
        if not isinstance(data["pointer"], str):
            raise ManagedLaunchError("Invalid saved uninstall pointer.")
        try:
            _selected(root, data["pointer"].encode("ascii"))
        except UnicodeError as error:
            raise ManagedLaunchError("Invalid saved uninstall pointer encoding.") from error
    if not check_pointer:
        return data
    pointer = _read(root / POINTER_NAME, 512, missing=True)
    if data["pointer"] != (None if pointer is None else pointer.decode("ascii")):
        raise ManagedLaunchError("The active pointer changed during uninstall.")
    if pointer is not None:
        _selected(root, pointer)
    return data


def prepare_managed_uninstall(executable: Path, registry=None) -> None:
    candidates = installer.installed_executables(executable)
    root = Path(executable).resolve(strict=True).parent.parent.parent
    registry = registry if registry is not None else shell.WinRegistry()
    with _root_guard(root) as root, registry.locked():
        _owner(root)
        _recover_locked(root, registry)
        path = root / UNINSTALL_NAME
        raw = _read(path, MAX_JOURNAL, missing=True)
        if raw is None:
            pointer = _read(root / POINTER_NAME, 512, missing=True)
            if pointer is not None:
                _selected(root, pointer)
            raw = _encoded({"version": 1, "state": "prepared", "owner": _owner(root).decode("ascii"),
                            "pointer": None if pointer is None else pointer.decode("ascii")})
            _cas_file(path, None, raw)
        _uninstall_receipt(root, raw)
        for candidate in candidates:
            shell.uninstall_if_executable(Path(_registry_path(candidate.resolve(strict=True))), registry=registry)


def finalize_managed_uninstall(executable: Path, registry=None) -> None:
    """Authorize, but do not delete, owned pointer state for Inno's final guard.

    The helper itself loads payload DLLs. Inno must wait for it to exit, acquire
    the named root mutex, retain exclusive binary handles, then compare every
    ready-receipt hash before deleting the exact files at its uninstall commit.
    A busy abort leaves the pointer and a reinstall-cancellable receipt intact.
    """
    candidates = installer.installed_executables(executable)
    root = Path(executable).resolve(strict=True).parent.parent.parent
    registry = registry if registry is not None else shell.WinRegistry()
    with _root_guard(root) as root, registry.locked():
        _owner(root)
        if _read(root / JOURNAL_NAME, MAX_JOURNAL, missing=True) is not None:
            raise ManagedLaunchError("An activation must be recovered before uninstalling.")
        raw = _read(root / UNINSTALL_NAME, MAX_JOURNAL)
        _uninstall_receipt(root, raw)
        document, _ = shell._read_manifest(registry)
        if document is not None:
            snapshots = ([document["data"]] if document["state"] == "active" else
                         [data for data in (document["base"], document["target"]) if data is not None])
            if any(shell._owned_by_executable(data, _registry_path(candidate.resolve(strict=True)))
                   for candidate in candidates for data in snapshots):
                raise ManagedLaunchError("This installation still owns registry entries; file deletion was refused.")
        pointer = _read(root / POINTER_NAME, 512, missing=True)
        ready = _ready_bytes(root, _owner(root), pointer, raw)
        existing = _read(root / READY_NAME, 512, missing=True)
        if existing is not None and existing != ready:
            raise ManagedLaunchError("The final-uninstall receipt changed; deletion was refused.")
        _cas_file(root / READY_NAME, existing, ready, 512)
