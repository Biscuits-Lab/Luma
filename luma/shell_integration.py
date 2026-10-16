"""Owned, per-user Explorer registration for the Windows x64 bundle.

Import, registration_plan(), and status() are read-only. Call install/uninstall
from setup, removal, or explicit registration repair. Default Apps choice is separate.

Different pre-existing values are never replaced except an exact, unowned
Applications open command for a validated prior payload of the same installation.
That one command becomes owned; its old bytes are kept only for failure rollback.
Identical values are never adopted. A bounded REG_SZ write-ahead journal is flushed
before target writes. Interrupted work is
rolled back using compare-and-restore, preserving third-party modifications.
Unknown manifests are never overwritten and key cleanup is never recursive.
"""
from __future__ import annotations

import contextlib
import ctypes
import functools
import json
import platform
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

REG_SZ = 1
CLASSES = r"Software\Classes"
CAPABILITIES = r"Software\Biscuits Lab\Luma\Capabilities"
REGISTERED_APPS = r"Software\RegisteredApplications"
METADATA_KEY = r"Software\Biscuits Lab\Luma\ShellIntegration"
MANIFEST_NAME = "Manifest"
THUMBNAIL_IID = "{E357FCCD-A995-4576-B01F-234630154E96}"
PHOTO_PROVIDER = "{C7657C4A-9F68-40FA-A4DF-96BC08EB3551}"
PROVIDERS = {
    "raster": "{A1D48846-7E23-4C76-B5A7-88FE510AEB06}",
    "hdr": "{5C7F845B-D0D3-4CC7-8A47-E240EBD08C56}",
    "exr": "{97144AB9-77CE-4B28-B32D-0A7A80DC57AA}",
    "raw": "{C9B8C1F5-D343-45D7-A5BA-44AA071D1D2B}",
}
RASTER = (
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".dib",
    ".tif", ".tiff", ".tga", ".dds", ".gif", ".ico", ".ppm", ".pgm",
    ".pbm", ".pnm",
)
RAW = (
    ".nef", ".nrw", ".cr2", ".cr3", ".crw", ".arw", ".srf", ".sr2",
    ".dng", ".raf", ".orf", ".rw2", ".raw", ".pef", ".ptx", ".srw",
    ".kdc", ".dcr", ".mrw", ".mos", ".3fr", ".fff", ".iiq", ".rwl", ".erf",
)
HDR = (".hdr", ".rgbe", ".pic")
EXR = (".exr",)
EXTENSIONS = RASTER + RAW + HDR + EXR
CUSTOM_RASTER = {".webp", ".tga", ".dds", ".ppm", ".pgm", ".pbm", ".pnm"}
MAX_ENTRIES = 600
MAX_KEYS = 1200
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_VALUE_CHARS = 4096
GUID = re.compile(
    r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}"
)


class ShellIntegrationError(RuntimeError):
    pass


class RegistrationConflict(ShellIntegrationError):
    pass


class InvalidManifest(ShellIntegrationError):
    pass


class RecoveryRequired(ShellIntegrationError):
    pass


@dataclass(frozen=True)
class Value:
    kind: int
    data: object


@dataclass(frozen=True)
class Write:
    key: str
    name: str
    value: Value


@dataclass(frozen=True)
class Result:
    changed_values: int
    preserved_values: int = 0
    recovered: bool = False


@dataclass(frozen=True)
class Status:
    state: str
    owned_values: int = 0
    modified_values: int = 0
    detail: str = ""
    thumbnails_enabled: bool = False


@dataclass(frozen=True)
class OwnedRegistration:
    """Read-only confirmation snapshot; the exact receipt is checked on apply."""
    executable: str
    manifest: Value


def _key(*parts: str) -> str:
    """Join fixed registry path fragments without empty components."""
    if not parts or any(
        not part or part.startswith("\\") or part.endswith("\\")
        or "\\\\" in part or "/" in part or "\0" in part
        for part in parts
    ):
        raise ShellIntegrationError("Invalid registry path fragment.")
    return "\\".join(parts)


def _windows_path(value: object, filename: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ShellIntegrationError("A bounded, absolute local Windows path is required.")
    if any(ord(char) < 32 for char in value):
        raise ShellIntegrationError("Control characters are not permitted in paths.")
    path = PureWindowsPath(value)
    if not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive):
        raise ShellIntegrationError("UNC, device, and relative paths are not supported.")
    if ".." in value.replace("/", "\\").split("\\")[1:]:
        raise ShellIntegrationError("Parent traversal is not permitted in bundle paths.")
    for part in path.parts[1:]:
        if (
            any(char in '<>:"|?*' for char in part)
            or part.endswith((" ", "."))
            or re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?",
                            part, re.IGNORECASE)
        ):
            raise ShellIntegrationError("The bundle path contains an invalid Windows name.")
    if path.name.casefold() != filename.casefold():
        raise ShellIntegrationError(f"The bundle must contain {filename}.")
    return str(path)


def _managed_launcher(executable: str) -> str:
    path = PureWindowsPath(_windows_path(executable, "Luma.exe"))
    if (path.parent.parent.name != "versions"
            or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)-[0-9a-f]{24}", path.parent.name)):
        raise InvalidManifest("Managed payload owner is outside the versioned layout.")
    return str(path.parent.parent.parent / "Luma.exe")


def registration_plan(
    executable: Path | str,
    existing_handlers: dict[str, str | None] | None = None,
    *, launch_executable: Path | str | None = None,
) -> tuple[Write, ...]:
    """Pure bounded declaration, including when called on Linux."""
    exe = _windows_path(str(executable), "Luma.exe")
    dll = str(PureWindowsPath(exe).parent / "LumaThumbnail.dll")
    if launch_executable is not None:
        launch = _windows_path(str(launch_executable), "Luma.exe")
        if launch.casefold() != _managed_launcher(exe).casefold():
            raise ShellIntegrationError("Managed launch entry must be the exact installation root.")
        exe = launch
    handlers = existing_handlers or {}
    writes: list[Write] = []

    def add(key: str, name: str, data: str) -> None:
        _key(key)
        writes.append(Write(key, name, Value(REG_SZ, data)))

    for mode, clsid in PROVIDERS.items():
        key = _key(CLASSES, "CLSID", clsid)
        add(key, "", f"Luma {mode.upper()} thumbnail provider")
        add(_key(key, "InprocServer32"), "", dll)
        add(_key(key, "InprocServer32"), "ThreadingModel", "Apartment")
    add(CAPABILITIES, "ApplicationName", "Luma")
    add(CAPABILITIES, "ApplicationDescription",
        "Luma image viewer and Explorer thumbnail support")
    add(CAPABILITIES, "ApplicationIcon", f'"{exe}",0')
    add(REGISTERED_APPS, "Luma", CAPABILITIES)
    app = _key(CLASSES, "Applications", "Luma.exe")
    add(app, "FriendlyAppName", "Luma")
    add(_key(app, "DefaultIcon"), "", f'"{exe}",0')
    add(_key(app, "shell", "open", "command"), "", f'"{exe}" "%1"')
    # Open With's executable route can select Applications\Luma.exe rather
    # than a per-format ProgID. This owned fallback sniffs the stream content;
    # the per-format route below still preserves common-image providers.
    add(_key(app, "shellex", THUMBNAIL_IID), "", PROVIDERS["raster"])

    for extension in EXTENSIONS:
        progid = "Luma.Image." + extension[1:]
        key = _key(CLASSES, progid)
        if extension in RAW:
            provider = PROVIDERS["raw"]
        elif extension in HDR:
            provider = PROVIDERS["hdr"]
        elif extension in EXR:
            provider = PROVIDERS["exr"]
        else:
            candidate = handlers.get(extension)
            if isinstance(candidate, str) and GUID.fullmatch(candidate):
                provider = candidate.upper()
            elif extension in CUSTOM_RASTER:
                provider = PROVIDERS["raster"]
            else:
                provider = PHOTO_PROVIDER
        add(key, "", "Luma image")
        add(_key(key, "DefaultIcon"), "", f'"{exe}",0')
        add(_key(key, "shell", "open", "command"), "", f'"{exe}" "%1"')
        add(_key(key, "shellex", THUMBNAIL_IID), "", provider)
        add(_key(CLASSES, extension, "OpenWithProgIDs"), progid, "")
        add(_key(CAPABILITIES, "FileAssociations"), extension, progid)
        add(_key(app, "SupportedTypes"), extension, "")
    if len(writes) > MAX_ENTRIES:
        raise ShellIntegrationError("Registration plan exceeds its fixed bound.")
    return tuple(writes)


_TEMPLATE = {(w.key, w.name): w.value for w in registration_plan(r"C:\Luma\Luma.exe")}
_APPLICATION_COMMAND = (_key(CLASSES, "Applications", "Luma.exe", "shell", "open", "command"), "")
_PROTECTED_KEYS = {
    "Software", CLASSES, _key(CLASSES, "CLSID"),
    _key(CLASSES, "Applications"), REGISTERED_APPS,
    r"Software\Biscuits Lab", r"Software\Biscuits Lab\Luma",
}


def _ancestors(key: str) -> list[str]:
    parts = key.split("\\")
    return ["\\".join(parts[:i]) for i in range(1, len(parts) + 1)]


_ALLOWED_KEYS = {
    parent for key, _ in _TEMPLATE for parent in _ancestors(key)
    if parent not in _PROTECTED_KEYS
}


def _encoded_value(value: Value | None):
    return None if value is None else {"type": value.kind, "data": value.data}


def _decoded_value(value):
    return None if value is None else Value(value["type"], value["data"])


def _address(record: dict) -> tuple[str, str]:
    return record["key"], record["name"]


def _path_role(address: tuple[str, str]) -> str | None:
    key, name = address
    if key.endswith(r"\InprocServer32") and name == "":
        return "dll"
    if key.endswith(r"\shell\open\command") and name == "":
        return "command"
    if (key.endswith(r"\DefaultIcon") and name == "") or (
        key == CAPABILITIES and name == "ApplicationIcon"
    ):
        return "icon"
    return None


def _validate_value(address: tuple[str, str], value, *, nullable=False) -> None:
    if value is None and nullable:
        return
    if (
        not isinstance(value, dict) or set(value) != {"type", "data"}
        or type(value["type"]) is not int or value["type"] != REG_SZ
        or not isinstance(value["data"], str)
        or len(value["data"]) > MAX_VALUE_CHARS or "\0" in value["data"]
    ):
        raise InvalidManifest("Manifest contains an unsupported registry value.")
    key, name = address
    data = value["data"]
    try:
        role = _path_role(address)
        if role == "dll":
            _windows_path(data, "LumaThumbnail.dll")
        elif role == "command":
            match = re.fullmatch(r'"([^"]+)" "%1"', data)
            if not match:
                raise InvalidManifest("Invalid command in manifest.")
            _windows_path(match[1], "Luma.exe")
        elif role == "icon":
            match = re.fullmatch(r'"([^"]+)",0', data)
            if not match:
                raise InvalidManifest("Invalid icon in manifest.")
            _windows_path(match[1], "Luma.exe")
        elif key.endswith("\\" + _key("shellex", THUMBNAIL_IID)) and name == "":
            if not GUID.fullmatch(data):
                raise InvalidManifest("Invalid thumbnail provider in manifest.")
        elif value != _encoded_value(_TEMPLATE[address]):
            raise InvalidManifest("Manifest value is outside the fixed registration plan.")
    except ShellIntegrationError as exc:
        raise InvalidManifest(str(exc)) from exc


def _validate_keys(keys) -> None:
    if (
        not isinstance(keys, list) or len(keys) > MAX_KEYS
        or any(not isinstance(key, str) or key not in _ALLOWED_KEYS for key in keys)
        or len(set(keys)) != len(keys)
    ):
        raise InvalidManifest("Manifest contains an unrecognized cleanup key.")


def _validate_data(data) -> None:
    if not isinstance(data, dict) or set(data) not in (
            {"entries", "keys", "thumbnails_enabled"},
            {"entries", "keys", "thumbnails_enabled", "managed_payload"}):
        raise InvalidManifest("Unrecognized ownership data.")
    if type(data["thumbnails_enabled"]) is not bool:
        raise InvalidManifest("Invalid thumbnail ownership state.")
    _validate_keys(data["keys"])
    entries = data["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise InvalidManifest("Ownership data exceeds its bound.")
    seen = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"key", "name", "previous", "written"}
            or not isinstance(entry["key"], str) or not isinstance(entry["name"], str)
        ):
            raise InvalidManifest("Unrecognized ownership entry.")
        address = _address(entry)
        if address not in _TEMPLATE or address in seen:
            raise InvalidManifest("Manifest contains an unrecognized or duplicate address.")
        seen.add(address)
        if entry["previous"] is not None:
            raise InvalidManifest("This manifest version cannot own a replaced value.")
        _validate_value(address, entry["written"])
    if "managed_payload" in data:
        owner = _windows_path(data["managed_payload"], "Luma.exe")
        _managed_launcher(owner)
        if not _owned_by_executable(data, owner):
            raise InvalidManifest("Managed owner does not match the recorded command, icon and DLL paths.")


def _entry_map(data) -> dict:
    return {} if data is None else {_address(e): e["written"] for e in data["entries"]}


def _managed_command_path(value) -> PureWindowsPath | None:
    """Recognize only the conventional command in the versioned payload layout.

    The installer separately validates the actual marker-bearing siblings before
    providing its allowlist. This structural check also constrains crash journals
    without trusting arbitrary saved addresses, arguments, or portable paths.
    """
    try:
        _validate_value(_APPLICATION_COMMAND, value)
    except ShellIntegrationError:
        return None
    executable = _windows_path(re.fullmatch(r'"([^"]+)" "%1"', value["data"])[1], "Luma.exe")
    if value["data"].casefold() != f'"{executable}" "%1"'.casefold():
        return None
    path = PureWindowsPath(executable)
    if (path.parent.parent.name.casefold() != "versions"
            or not re.fullmatch(r"\d+\.\d+\.\d+-[0-9a-f]{24}", path.parent.name, re.IGNORECASE)):
        return None
    return path


def _same_installation_command_move(before, after) -> bool:
    old, new = _managed_command_path(before), _managed_command_path(after)
    if old and new and old != new and old.parent.parent == new.parent.parent:
        return True
    if old:
        try:
            _validate_value(_APPLICATION_COMMAND, after)
            return after["data"].casefold() == f'"{_managed_launcher(str(old))}" "%1"'.casefold()
        except ShellIntegrationError:
            pass
    return False


def _validate_document(document) -> None:
    if (
        not isinstance(document, dict)
        or type(document.get("version")) is not int or document.get("version") not in (1, 2, 3)
    ):
        raise InvalidManifest("Unknown shell-integration manifest version.")
    if document.get("state") == "active":
        if document["version"] != 1 or set(document) != {"version", "state", "data"}:
            raise InvalidManifest("Unrecognized active manifest.")
        _validate_data(document["data"])
        return
    fields = {
        "version", "state", "base", "target", "changes", "created"
    }
    managed_pointer = document["version"] == 3
    if managed_pointer:
        fields.add("managed_pointer_transaction")
        if document.get("managed_pointer_transaction") is not True:
            raise InvalidManifest("Invalid managed pointer transaction marker.")
    adopting = document["version"] == 2 or (managed_pointer and "adopted_application_command" in document)
    if adopting:
        fields.add("adopted_application_command")
        if document.get("adopted_application_command") is not True:
            raise InvalidManifest("Invalid application-command adoption journal.")
    if document.get("state") != "pending" or set(document) != fields:
        raise InvalidManifest("Unrecognized journal.")
    base, target = document["base"], document["target"]
    if base is None and target is None:
        raise InvalidManifest("Empty transaction.")
    for data in (base, target):
        if data is not None:
            _validate_data(data)
    _validate_keys(document["created"])
    if target is None:
        if document["created"]:
            raise InvalidManifest("Uninstall cannot create registration keys.")
    elif set(target["keys"]) != (
        set(base["keys"] if base else []) | set(document["created"])
    ):
        raise InvalidManifest("Journal cleanup ownership is inconsistent.")
    changes = document["changes"]
    if not isinstance(changes, list) or len(changes) > MAX_ENTRIES:
        raise InvalidManifest("Journal exceeds its bound.")
    before, after = _entry_map(base), _entry_map(target)
    if adopting:
        adoption = [change for change in changes if isinstance(change, dict)
                    and (change.get("key"), change.get("name")) == _APPLICATION_COMMAND]
        if (target is None or _APPLICATION_COMMAND in before or len(adoption) != 1
                or not _same_installation_command_move(adoption[0].get("before"),
                                                       adoption[0].get("after"))):
            raise InvalidManifest("Application-command adoption is outside its narrow scope.")
        # Only the one unowned same-installation command can contribute a
        # rollback value absent from base ownership. Active receipts stay v1.
        before[_APPLICATION_COMMAND] = adoption[0]["before"]
    seen = set()
    for change in changes:
        if (
            not isinstance(change, dict)
            or set(change) != {"key", "name", "before", "after"}
            or not isinstance(change["key"], str) or not isinstance(change["name"], str)
        ):
            raise InvalidManifest("Unrecognized journal operation.")
        address = _address(change)
        if address not in _TEMPLATE or address in seen:
            raise InvalidManifest("Journal contains an unrecognized or duplicate address.")
        seen.add(address)
        _validate_value(address, change["before"], nullable=True)
        _validate_value(address, change["after"], nullable=True)
        if (
            change["before"] == change["after"]
            or change["before"] != before.get(address)
            or change["after"] != after.get(address)
        ):
            raise InvalidManifest("Journal snapshots do not match ownership data.")
    if target is not None:
        expected = {
            address for address in before.keys() | after.keys()
            if before.get(address) != after.get(address)
        }
        if seen != expected:
            raise InvalidManifest("Journal is incomplete.")
    for key in document["created"]:
        if not any(
            change["after"] is not None
            and (change["key"] == key or change["key"].startswith(key + "\\"))
            for change in changes
        ):
            raise InvalidManifest("Journal has an unrelated cleanup key.")


def _no_duplicate_members(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member.")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _manifest_value(document) -> Value | None:
    if document is None:
        return None
    _validate_document(document)
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise InvalidManifest("Manifest exceeds its size bound.")
    return Value(REG_SZ, text)


def _read_manifest(registry):
    raw = registry.read(METADATA_KEY, MANIFEST_NAME)
    if raw is None:
        return None, None
    if raw.kind != REG_SZ or not isinstance(raw.data, str) or len(raw.data) > MAX_MANIFEST_BYTES:
        raise InvalidManifest("An unrecognized manifest is present; it was left untouched.")
    try:
        if len(raw.data.encode("utf-8")) > MAX_MANIFEST_BYTES:
            raise ValueError("Manifest is too large.")
        document = json.loads(
            raw.data, object_pairs_hook=_no_duplicate_members,
            parse_constant=_reject_constant,
        )
        _validate_document(document)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise InvalidManifest("The existing manifest is invalid and was left untouched.") from exc
    return document, raw


def _write_manifest(registry, document, expected):
    if registry.read(METADATA_KEY, MANIFEST_NAME) != expected:
        raise RegistrationConflict("The ownership manifest changed during the operation.")
    value = _manifest_value(document)
    if value is None:
        registry.delete_value(METADATA_KEY, MANIFEST_NAME)
    else:
        registry.write(METADATA_KEY, MANIFEST_NAME, value)
    registry.flush()
    return value


def _active(data):
    return None if data is None else {"version": 1, "state": "active", "data": data}


def _cleanup_empty_keys(registry, keys) -> None:
    for key in sorted(set(keys), key=lambda item: item.count("\\"), reverse=True):
        try:
            registry.delete_empty_key(key)
        except OSError:
            pass  # Leaving an empty key is safe; recursive removal is forbidden.


def _rollback(registry, journal, allowed_manifests, *, restore_manifest=None) -> None:
    raw = registry.read(METADATA_KEY, MANIFEST_NAME)
    if not any(raw == candidate for candidate in allowed_manifests):
        raise RecoveryRequired("The journal changed; automatic recovery was stopped.")
    for change in reversed(journal["changes"]):
        key, name = _address(change)
        after = _decoded_value(change["after"])
        before = _decoded_value(change["before"])
        if registry.read(key, name) != after:
            continue  # Already restored, or modified by another program.
        if before is None:
            registry.delete_value(key, name)
        else:
            registry.write(key, name, before)
    registry.flush()
    _cleanup_empty_keys(registry, journal["created"])
    if restore_manifest is None:
        _write_manifest(registry, _active(journal["base"]), raw)
    else:
        if registry.read(METADATA_KEY, MANIFEST_NAME) != raw:
            raise RecoveryRequired("The registry receipt changed before exact restoration.")
        registry.write(METADATA_KEY, MANIFEST_NAME, restore_manifest)
        registry.flush()
    if journal["base"] is None:
        _cleanup_empty_keys(registry, [METADATA_KEY])
    registry.notify_associations_changed()


def _recover(registry):
    document, raw = _read_manifest(registry)
    recovered = False
    if document is not None and document["state"] == "pending":
        if document.get("managed_pointer_transaction") is True:
            raise RecoveryRequired("The interrupted managed activation must recover its pointer and registry together through Setup.")
        try:
            _rollback(registry, document, (raw,))
        except Exception as exc:
            raise RecoveryRequired(
                "An interrupted registration needs recovery. No elevation is attempted."
            ) from exc
        recovered = True
        document, raw = _read_manifest(registry)
    return (document["data"] if document else None), raw, recovered


def _transaction(registry, base, target, changes, created, previous_manifest,
                 *, adopted_application_command=False, participant=None) -> None:
    if base == target and not changes and participant is None:
        return
    journal = {
        "version": 1, "state": "pending", "base": base, "target": target,
        "changes": changes, "created": created,
    }
    if adopted_application_command:
        journal["version"] = 2
        journal["adopted_application_command"] = True
    if participant is not None:
        journal["version"] = 3
        journal["managed_pointer_transaction"] = True
    pending_value = _manifest_value(journal)
    target_value = _manifest_value(_active(target))
    # A managed pointer participant persists its recovery record before the
    # registry journal. It never accepts arbitrary registry addresses.
    if participant is not None:
        participant.prepare(registry, journal, previous_manifest)
    # Persist and flush the entire journal before ANY target registry write.
    try:
        _write_manifest(registry, journal, previous_manifest)
    except RegistrationConflict:
        raise
    except Exception as exc:
        raise RecoveryRequired(
            "The journal could not be persisted. No registration writes were attempted."
        ) from exc
    try:
        for change in changes:
            key, name = _address(change)
            if registry.read(key, name) != _decoded_value(change["before"]):
                raise RegistrationConflict(f"A registration changed: {key} [{name}]")
            after = _decoded_value(change["after"])
            if after is None:
                registry.delete_value(key, name)
            else:
                registry.write(key, name, after)
        registry.flush()
        if participant is not None:
            participant.activate(registry)
        _write_manifest(registry, _active(target), pending_value)
    except Exception as exc:
        try:
            _rollback(registry, journal, (pending_value, target_value),
                      restore_manifest=previous_manifest if participant is not None else None)
            if participant is not None:
                participant.rollback(registry)
        except Exception as recovery_error:
            raise RecoveryRequired(
                "Registration failed and recovery is incomplete. The journal was "
                "retained where possible; retry the Settings action."
            ) from recovery_error
        raise ShellIntegrationError(
            "Registration failed; completed writes were rolled back. "
            "Values changed by another program were preserved."
        ) from exc
    if participant is not None:
        participant.finish(registry)
    if target is None:
        _cleanup_empty_keys(registry, base["keys"] if base else [])
        _cleanup_empty_keys(registry, [METADATA_KEY])
    registry.notify_associations_changed()


def _runtime_guard() -> None:
    if sys.platform != "win32":
        raise ShellIntegrationError("Explorer integration is available only on Windows.")
    if not getattr(sys, "frozen", False):
        raise ShellIntegrationError(
            "Explorer integration requires the packaged portable Luma.exe; "
            "source and development runs cannot register it."
        )
    if struct.calcsize("P") != 8 or platform.machine().upper() not in {"AMD64", "X86_64"}:
        raise ShellIntegrationError("This thumbnail component requires the Windows x64 bundle.")


def _check_pe(path: Path, *, dll: bool) -> None:
    with path.open("rb") as stream:
        dos = stream.read(64)
        if len(dos) != 64 or dos[:2] != b"MZ":
            raise ShellIntegrationError(f"{path.name} is not a Windows PE binary.")
        offset = struct.unpack_from("<I", dos, 60)[0]
        if offset < 64 or offset > 1024 * 1024:
            raise ShellIntegrationError(f"{path.name} has an invalid PE header.")
        stream.seek(offset)
        header = stream.read(24)
        magic = stream.read(2)
    if (
        len(header) != 24 or header[:4] != b"PE\0\0"
        or struct.unpack_from("<H", header, 4)[0] != 0x8664
        or magic != b"\x0b\x02"
        or bool(struct.unpack_from("<H", header, 22)[0] & 0x2000) != dll
    ):
        raise ShellIntegrationError(f"{path.name} must be a Windows x64 PE binary.")


def _checked_executable(executable: Path) -> str:
    exe = Path(executable).resolve(strict=True)
    exe_text = _windows_path(str(exe), "Luma.exe")
    dll = (exe.parent / "LumaThumbnail.dll").resolve(strict=True)
    _windows_path(str(dll), "LumaThumbnail.dll")
    if not exe.is_file() or not dll.is_file() or dll.parent != exe.parent:
        raise ShellIntegrationError("Luma.exe and LumaThumbnail.dll must share one folder.")
    get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
    get_drive_type.argtypes = [ctypes.c_wchar_p]
    get_drive_type.restype = ctypes.c_uint
    if get_drive_type(PureWindowsPath(exe_text).anchor) not in {2, 3, 6}:
        raise ShellIntegrationError("The portable bundle must be on a local drive.")
    _check_pe(exe, dll=False)
    _check_pe(dll, dll=True)
    return exe_text


def _public_errors(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except OSError as exc:
            raise ShellIntegrationError(
                f"Windows could not complete the operation: {exc}. "
                "No elevation is attempted."
            ) from exc
    return wrapped


@_public_errors
def install(executable: Path, registry=None, *, allowed_previous_executables=None,
            expected_registration: OwnedRegistration | None = None,
            launch_executable: Path | None = None, _participant=None) -> Result:
    """Register owned entries; never select or replace Windows defaults.

    Automatic setup supplies an exact owner allowlist. Confirmed cross-copy repair
    also supplies the displayed ownership snapshot, checked under the same lock
    before recovering a pending transaction or writing any registry values.
    """
    _runtime_guard()
    exe = _checked_executable(executable)
    if (expected_registration is not None
            and exe.casefold() != _windows_path(str(executable), "Luma.exe").casefold()):
        raise RegistrationConflict("The confirmed installer executable path changed.")
    launch = None
    if launch_executable is not None:
        launch = _windows_path(str(launch_executable), "Luma.exe")
        if launch.casefold() != _managed_launcher(exe).casefold() or _participant is None:
            raise ShellIntegrationError("Managed registration requires its pointer transaction.")
    elif _participant is not None:
        raise ShellIntegrationError("A pointer transaction requires the managed launch entry.")
    allowed = None
    if allowed_previous_executables is not None:
        allowed = {_windows_path(str(path), "Luma.exe").casefold()
                   for path in allowed_previous_executables}
        if len(allowed) > MAX_ENTRIES:
            raise ShellIntegrationError("Automatic registration owner list exceeds its bound.")
        allowed.add(exe.casefold())
    registry = registry if registry is not None else WinRegistry()
    with registry.locked():
        if expected_registration is not None:
            if (not isinstance(expected_registration, OwnedRegistration) or allowed is None
                    or expected_registration.executable.casefold() not in allowed):
                raise RegistrationConflict("Repair requires its exact confirmed owner allowlist.")
            if _registration_owner_locked(registry) != expected_registration:
                raise RegistrationConflict(
                    "Explorer registration changed after confirmation. It was left untouched.")
        if allowed is not None:
            document, _ = _read_manifest(registry)
            if document is not None:
                snapshots = ([document["data"]] if document["state"] == "active" else
                             [data for data in (document["base"], document["target"])
                              if data is not None])
                if not snapshots or not all(any(_owned_by_executable(data, owner)
                                                for owner in allowed) for data in snapshots):
                    raise RegistrationConflict(
                        "Explorer registration belongs to another Luma copy. It was left untouched. "
                        "Continue using the registered copy; this copy will not replace its registration automatically.")
                if document.get("adopted_application_command") is True:
                    adoption = next(change for change in document["changes"]
                                    if _address(change) == _APPLICATION_COMMAND)
                    if str(_managed_command_path(adoption["before"])).casefold() not in allowed:
                        raise RegistrationConflict(
                            "Interrupted registration refers to a prior payload outside the validated "
                            "installation. Its command was left untouched.")
        if expected_registration is None:
            base, raw, recovered = _recover(registry)
        else:
            # A confirmed migration never recovers a journal introduced after
            # the prompt. Keep the exact confirmed receipt as the transaction's
            # compare-and-write precondition, even against non-cooperating apps.
            document, raw = _read_manifest(registry)
            if raw != expected_registration.manifest:
                raise RegistrationConflict(
                    "Explorer registration changed after confirmation. It was left untouched.")
            base, recovered = document["data"], False
        if launch is None and base is not None and "managed_payload" in base:
            try:
                same_root = _managed_launcher(exe).casefold() == _managed_launcher(base["managed_payload"]).casefold()
            except ShellIntegrationError:
                same_root = False
            if same_root:
                raise RegistrationConflict("Managed registration must keep its stable launch entry; use the managed repair route.")
        handlers = {ext: registry.provider_for(ext) for ext in RASTER}
        old_entries = {_address(e): e for e in (base["entries"] if base else [])}
        for extension in RASTER:
            address = (_key(CLASSES, "Luma.Image." + extension[1:], "shellex", THUMBNAIL_IID), "")
            old = old_entries.get(address)
            # Selecting our generic executable handler must not replace the
            # common-image provider retained on an existing per-format ProgID.
            if old is not None and handlers[extension] == PROVIDERS["raster"]:
                handlers[extension] = old["written"]["data"]
        entries, changes, conflicts = [], [], []
        preserved = 0
        adopted_application_command = False
        for write in registration_plan(exe, handlers, launch_executable=launch):
            address = (write.key, write.name)
            current = registry.read(*address)
            old = old_entries.get(address)
            desired = _encoded_value(write.value)
            if old is not None:
                if current != _decoded_value(old["written"]):
                    conflicts.append(address)
                    continue
                entries.append({
                    "key": write.key, "name": write.name,
                    "previous": old["previous"], "written": desired,
                })
            elif current is None:
                entries.append({
                    "key": write.key, "name": write.name,
                    "previous": None, "written": desired,
                })
            elif current == write.value:
                preserved += 1
                continue
            elif (address == _APPLICATION_COMMAND and allowed is not None
                  and _same_installation_command_move(_encoded_value(current), desired)
                  and str(_managed_command_path(_encoded_value(current))).casefold() in allowed):
                # A validated sibling command created by Windows Browse/Open
                # With becomes Luma-owned. Restore its exact old bytes only if
                # this transaction fails; successful uninstall must remove it.
                entries.append({"key": write.key, "name": write.name,
                                "previous": None, "written": desired})
                adopted_application_command = True
            else:
                conflicts.append(address)
                continue
            if current != write.value:
                changes.append({
                    "key": write.key, "name": write.name,
                    "before": _encoded_value(current), "after": desired,
                })
        if conflicts:
            addresses = "; ".join(f"{key} [{name}]" for key, name in conflicts[:4])
            raise RegistrationConflict(
                f"{len(conflicts)} registration value(s) belong to another writer "
                f"or changed since installation. They were left untouched: {addresses}"
            )
        if not entries and base is None:
            if _participant is not None:
                raise RegistrationConflict("Managed activation requires an actual owned registration receipt.")
            return Result(0, preserved, recovered)
        created = sorted({
            key for change in changes for key in _ancestors(change["key"])
            if key in _ALLOWED_KEYS and not registry.key_exists(key)
        })
        target = {
            "entries": entries,
            "keys": sorted(set(base["keys"] if base else []) | set(created)),
            "thumbnails_enabled": True,
        }
        if launch is not None:
            target["managed_payload"] = exe
        _transaction(registry, base, target, changes, created, raw,
                     adopted_application_command=adopted_application_command,
                     participant=_participant)
        return Result(len(changes), preserved, recovered)


def prepare_startup_registration(executable: Path, registry=None) -> Result:
    """One background startup check for packaged Windows; no default changes.

    A healthy current-copy manifest takes the read-only path. Setup of a fresh
    portable copy or upgrade of its own old plan uses the normal transaction.
    Another bundle's ownership and external modifications are never adopted.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return Result(0)
    _runtime_guard()
    exe = _checked_executable(executable)
    registry = registry if registry is not None else WinRegistry()
    path = Path(executable)
    managed = ((path.parent / "luma-install.json").exists()
               or path.parent.parent.name == "versions")
    if managed:
        from .managed_launch import read_active_payload
        selected = read_active_payload(path.parent.parent.parent)
        if selected.executable != path.resolve(strict=True):
            raise RegistrationConflict("This payload is not the selected managed version; its registration was preserved.")
    with registry.locked():
        document, _ = _read_manifest(registry)
        if document is not None and document["state"] == "active":
            data = document["data"]
            if not _owned_by_executable(data, exe):
                raise RegistrationConflict(
                    "Explorer registration belongs to another Luma copy. "
                    "Continue using the registered copy; this copy will not replace its registration automatically.")
            if any(registry.read(e["key"], e["name"]) != _decoded_value(e["written"])
                   for e in data["entries"]):
                raise RegistrationConflict(
                    "Another app changed Luma's Explorer registration. Those changes were preserved.")
            owned = {_address(e) for e in data["entries"]}
            if data["thumbnails_enabled"] and set(_TEMPLATE) <= owned:
                return Result(0)
    if managed:
        from .managed_launch import activate_managed_installation
        return activate_managed_installation(path, registry=registry,
                                             expected_active_pointer=selected.pointer())
    return install(Path(exe), registry=registry, allowed_previous_executables=(exe,))


def _uninstall_locked(registry) -> Result:
    document, _ = _read_manifest(registry)
    if document is not None and document.get("adopted_application_command") is True:
        # Rolling back an uncompleted adoption restores an unowned old command.
        # Let setup repair it first; an uninstaller must not then delete that
        # command's payload while incorrectly reporting that cleanup completed.
        raise RecoveryRequired(
            "An interrupted Open With migration needs registration repair before uninstalling. "
            "No application files or registration values were removed.")
    base, raw, recovered = _recover(registry)
    if base is None:
        return Result(0, 0, recovered)
    changes = []
    preserved = 0
    for entry in base["entries"]:
        key, name = _address(entry)
        if registry.read(key, name) != _decoded_value(entry["written"]):
            preserved += 1
            continue
        changes.append({
            "key": key, "name": name,
            "before": entry["written"], "after": entry["previous"],
        })
    _transaction(registry, base, None, changes, [], raw)
    return Result(len(changes), preserved, recovered)


@_public_errors
def uninstall(registry=None) -> Result:
    """Explicit removal action; preserves third-party changes and defaults."""
    _runtime_guard()
    registry = registry if registry is not None else WinRegistry()
    with registry.locked():
        return _uninstall_locked(registry)


def _owned_by_executable(data, executable: str) -> bool:
    owner = data.get("managed_payload")
    if owner is not None and owner.casefold() != str(executable).casefold():
        return False
    launch = _managed_launcher(owner) if owner is not None else None
    desired = {(w.key, w.name): w.value for w in
               registration_plan(executable, launch_executable=launch)}
    paths = [e for e in data["entries"] if _path_role(_address(e))]
    # Refuse ambiguous ownership, including a manifest with only non-path values.
    if not any(_path_role(_address(e)) in {"command", "icon"} for e in paths):
        return False
    return all(
        e["written"]["data"].casefold() == desired[_address(e)].data.casefold()
        for e in paths
    )


def _registration_owner_locked(registry) -> OwnedRegistration:
    document, raw = _read_manifest(registry)
    if document is None or document["state"] != "active":
        raise RegistrationConflict("Repair requires an active Luma ownership manifest.")
    data = document["data"]
    owner = data.get("managed_payload") or next((entry["written"]["data"].split('"')[1]
                  for entry in data["entries"]
                  if _path_role(_address(entry)) in {"command", "icon"}), None)
    if owner is None:
        raise RegistrationConflict("The existing Luma registration has no exact executable owner.")
    owner = _windows_path(owner, "Luma.exe")
    if not _owned_by_executable(data, owner):
        raise RegistrationConflict("The existing Luma registration has inconsistent executable owners.")
    if any(registry.read(*_address(entry)) != _decoded_value(entry["written"])
           for entry in data["entries"]):
        raise RegistrationConflict(
            "Another app changed Luma's Explorer registration. Those changes were preserved.")
    return OwnedRegistration(owner, raw)


@_public_errors
def registration_owner(registry=None) -> OwnedRegistration:
    """Inspect an intact active receipt without recovery or filesystem access.

    Every owned command, icon and DLL path must agree. The prior executable may
    have been removed; it is never opened, run, resolved or required to exist.
    """
    if registry is None:
        _runtime_guard()
        registry = WinRegistry()
    with registry.locked():
        return _registration_owner_locked(registry)


@_public_errors
def uninstall_if_executable(executable: Path, registry=None) -> Result:
    """Installer-only exact-owner cleanup; never recovers another bundle's journal.

    An entirely unrelated owner is a successful no-op. Mixed ownership raises
    RegistrationConflict so an installer cannot remove referenced files. For a
    pending move, both snapshots must match before recovery:
    an old uninstaller must not restore or remove the newer bundle's ownership.
    Unknown manifests remain errors and are never overwritten.
    """
    _runtime_guard()
    exe = _windows_path(str(executable), "Luma.exe")
    if registry is None:
        exe = _windows_path(str(Path(exe).resolve(strict=False)), "Luma.exe")
        registry = WinRegistry()
    with registry.locked():
        document, _ = _read_manifest(registry)
        if document is None:
            return Result(0)
        snapshots = (
            [document["data"]] if document["state"] == "active"
            else [data for data in (document["base"], document["target"]) if data is not None]
        )
        matches = [_owned_by_executable(data, exe) for data in snapshots]
        if any(matches) and not all(matches):
            raise RegistrationConflict(
                "An interrupted operation spans different bundles. Repair the "
                "registration in Settings before removing either bundle."
            )
        if not matches or not all(matches):
            return Result(0)
        return _uninstall_locked(registry)


def _is_luma_thumbnail_entry(entry) -> bool:
    key, name = _address(entry)
    if any(
        key == _key(CLASSES, "CLSID", clsid)
        or key.startswith(_key(CLASSES, "CLSID", clsid) + "\\")
        for clsid in PROVIDERS.values()
    ):
        return True
    return (
        name == "" and key.endswith("\\" + _key("shellex", THUMBNAIL_IID))
        and entry["written"]["data"].upper() in PROVIDERS.values()
    )


@_public_errors
def disable_thumbnails(registry=None) -> Result:
    """Explicit thumbnail removal: retain Luma's usable file-opening registrations.

    Keep Photo/third-party handlers. Remove only unchanged owned Luma thumbnail
    handlers and COM values. Retain ownership records for modified values so
    status continues to disclose conflicts and a later install cannot adopt them.
    """
    _runtime_guard()
    registry = registry if registry is not None else WinRegistry()
    with registry.locked():
        base, raw, recovered = _recover(registry)
        if base is None:
            return Result(0, 0, recovered)
        entries, changes = [], []
        preserved = 0
        for entry in base["entries"]:
            if not _is_luma_thumbnail_entry(entry):
                entries.append(entry)
                continue
            key, name = _address(entry)
            if registry.read(key, name) != _decoded_value(entry["written"]):
                entries.append(entry)
                preserved += 1
                continue
            changes.append({
                "key": key, "name": name,
                "before": entry["written"], "after": entry["previous"],
            })
        target = {
            "entries": entries, "keys": list(base["keys"]),
            "thumbnails_enabled": False,
        }
        if "managed_payload" in base:
            target["managed_payload"] = base["managed_payload"]
        _transaction(registry, base, target, changes, [], raw)
        return Result(len(changes), preserved, recovered)


def _stale_paths(entries, executable: str) -> bool:
    desired = {(w.key, w.name): w.value for w in registration_plan(executable)}
    return any(
        _path_role(_address(entry))
        and _decoded_value(entry["written"]) != desired[_address(entry)]
        for entry in entries
    )


def status(registry=None) -> Status:
    """Read only, including when an interrupted-operation journal is present."""
    native = registry is None
    if native:
        try:
            _runtime_guard()
            registry = WinRegistry()
        except ShellIntegrationError as exc:
            return Status("unavailable", detail=str(exc))
    try:
        document, _ = _read_manifest(registry)
        if document is None:
            return Status("not_installed")
        if document["state"] == "pending":
            return Status(
                "recovery_required",
                detail="An interrupted registration will be recovered on the next "
                "explicit Settings action.",
                thumbnails_enabled=bool(
                    document["base"] and document["base"]["thumbnails_enabled"]
                ),
            )
        entries = document["data"]["entries"]
        changed = sum(
            registry.read(e["key"], e["name"]) != _decoded_value(e["written"])
            for e in entries
        )
        enabled = document["data"]["thumbnails_enabled"]
        state = "modified" if changed else ("installed" if enabled else "association_only")
        detail = ""
        if changed:
            detail = f"{changed} registration value(s) were changed externally and preserved."
        if not changed and native and not _owned_by_executable(document["data"], sys.executable):
            state = "stale"
            detail = "Registration points to another bundle location; use Repair."
        if native and enabled and state == "installed":
            detail = "\n".join(_thumbnail_association_detail(registry, extension)
                               for extension in (".hdr", ".exr"))
        return Status(state, len(entries), changed, detail, enabled)
    except (ShellIntegrationError, OSError) as exc:
        return Status("blocked", detail=str(exc))


def _association_string(extension: str, kind: int, extra: str | None = None) -> str | None:
    """Ask the Shell's association resolver, without verification or fixups.

    A hand-written registry search misses base/perceived-type providers and can
    disagree with the user's effective association. Output is always bounded.
    This query does not activate a provider or load its DLL.
    """
    if extension not in EXTENSIONS or kind not in (16, 20):
        raise ShellIntegrationError("Unsupported association query.")
    from ctypes import wintypes
    query = ctypes.WinDLL("shlwapi").AssocQueryStringW
    query.argtypes = [wintypes.DWORD, ctypes.c_int, wintypes.LPCWSTR,
                      wintypes.LPCWSTR, wintypes.LPWSTR,
                      ctypes.POINTER(wintypes.DWORD)]
    query.restype = ctypes.c_long
    result = ctypes.create_unicode_buffer(MAX_VALUE_CHARS)
    length = wintypes.DWORD(len(result))
    # ASSOCF_NOTRUNCATE | ASSOCF_NOFIXUPS; respect per-user choices.
    hr = query(0x00000020 | 0x00000100, kind, extension, extra,
               result, ctypes.byref(length))
    if hr != 0 or not 1 < length.value <= len(result):
        return None
    text = result.value
    return text if text and len(text) < len(result) else None


def _thumbnail_association_detail(registry, extension: str) -> str:
    """Read the effective association; a receipt alone cannot establish selection."""
    if extension not in (".hdr", ".exr"):
        raise ValueError("Unsupported thumbnail status extension")
    mode = extension[1:]
    try:
        provider = registry.effective_provider_for(extension)
        if provider in (PROVIDERS[mode], PROVIDERS["raster"]):
            return f"Windows selects Luma's thumbnail handler for {extension}."
        progid = _association_string(extension, 20)  # ASSOCSTR_PROGID
    except OSError:
        return f"The current {extension} thumbnail association could not be checked."
    detail = (f"Windows currently selects another thumbnail provider for {extension}."
              if provider else f"Windows did not report a thumbnail provider for {extension}.")
    if progid and progid.casefold() == r"Applications\Luma.exe".casefold():
        detail += " This file type uses the executable's Open With association."
    return detail + f" Choose Luma for {extension} in Windows Default Apps, then reopen the folder."


def _hdr_association_detail(registry) -> str:
    """Compatibility helper for the original HDR-only status API."""
    return _thumbnail_association_detail(registry, ".hdr")


def _user_mutex_name() -> str:
    """Stable per-user name across interactive sessions, derived from token SID."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)
    ]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    sid_text = ctypes.c_wchar_p()
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not 0 < needed.value <= 65536:
            raise ShellIntegrationError("Could not determine the current user token.")
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(
            token, 1, buffer, needed.value, ctypes.byref(needed)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is PSID.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        return "Global\\BiscuitsLab.Luma.ShellIntegration." + sid_text.value
    finally:
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        kernel.CloseHandle(token)


class WinRegistry:
    """HKCU-only writer; HKCR and UserChoice lookups are read-only."""

    def __init__(self):
        if sys.platform != "win32":
            raise ShellIntegrationError("The Windows registry adapter requires Windows.")
        import winreg
        self.w = winreg
        self.view = winreg.KEY_WOW64_64KEY

    def _read(self, root, key, name):
        _key(key)
        try:
            with self.w.OpenKey(root, key, 0, self.w.KEY_READ | self.view) as handle:
                data, kind = self.w.QueryValueEx(handle, name)
            return Value(kind, data)
        except FileNotFoundError:
            return None

    def read(self, key, name):
        return self._read(self.w.HKEY_CURRENT_USER, key, name)

    @staticmethod
    def _allowed_address(key, name):
        if (key, name) not in _TEMPLATE and (key, name) != (METADATA_KEY, MANIFEST_NAME):
            raise ShellIntegrationError("Refusing a write outside the fixed registry plan.")
        _key(key)

    def write(self, key, name, value):
        self._allowed_address(key, name)
        if value.kind != REG_SZ or not isinstance(value.data, str):
            raise ShellIntegrationError("Only bounded string registration values are supported.")
        if (key, name) == (METADATA_KEY, MANIFEST_NAME):
            if len(value.data.encode("utf-8")) > MAX_MANIFEST_BYTES:
                raise InvalidManifest("Manifest exceeds its size bound.")
        else:
            _validate_value((key, name), _encoded_value(value))
        with self.w.CreateKeyEx(
            self.w.HKEY_CURRENT_USER, key, 0,
            self.w.KEY_SET_VALUE | self.w.KEY_QUERY_VALUE | self.view,
        ) as handle:
            self.w.SetValueEx(handle, name, 0, value.kind, value.data)

    def delete_value(self, key, name):
        self._allowed_address(key, name)
        try:
            with self.w.OpenKey(
                self.w.HKEY_CURRENT_USER, key, 0, self.w.KEY_SET_VALUE | self.view
            ) as handle:
                self.w.DeleteValue(handle, name)
        except FileNotFoundError:
            pass

    def key_exists(self, key):
        _key(key)
        try:
            with self.w.OpenKey(
                self.w.HKEY_CURRENT_USER, key, 0, self.w.KEY_READ | self.view
            ):
                return True
        except FileNotFoundError:
            return False

    def delete_empty_key(self, key):
        if key not in _ALLOWED_KEYS and key != METADATA_KEY:
            raise ShellIntegrationError("Refusing cleanup outside known registration keys.")
        try:
            with self.w.OpenKey(
                self.w.HKEY_CURRENT_USER, key, 0, self.w.KEY_READ | self.view
            ) as handle:
                subkeys, values, _ = self.w.QueryInfoKey(handle)
            if subkeys or values:
                return
            self.w.DeleteKeyEx(self.w.HKEY_CURRENT_USER, key, self.view, 0)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if getattr(exc, "winerror", None) != 145:
                raise

    def flush(self):
        self.w.FlushKey(self.w.HKEY_CURRENT_USER)

    def provider_for(self, extension):
        if extension not in RASTER:
            return None
        try:
            effective = self.effective_provider_for(extension)
        except OSError:
            effective = None
        if effective:
            return effective
        # Conservative fallback if the Shell cannot resolve an association.
        # These reads never repair defaults or replace foreign values.
        user_choice = self._read(
            self.w.HKEY_CURRENT_USER,
            _key(r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts",
                 extension, "UserChoice"),
            "ProgId",
        )
        extension_default = self._read(self.w.HKEY_CLASSES_ROOT, extension, "")
        candidates = []
        for value in (user_choice, extension_default):
            if (
                value is not None and value.kind == REG_SZ
                and isinstance(value.data, str) and 0 < len(value.data) <= 255
                and "\\" not in value.data and "/" not in value.data
                and "\0" not in value.data
            ):
                candidates.append(_key(value.data, "shellex", THUMBNAIL_IID))
        candidates.extend((
            _key(extension, "shellex", THUMBNAIL_IID),
            _key("SystemFileAssociations", extension, "shellex", THUMBNAIL_IID),
        ))
        for key in candidates:
            value = self._read(self.w.HKEY_CLASSES_ROOT, key, "")
            if (
                value is not None and value.kind == REG_SZ
                and isinstance(value.data, str) and GUID.fullmatch(value.data)
            ):
                return value.data.upper()
        return None

    def effective_provider_for(self, extension):
        value = _association_string(extension, 16, THUMBNAIL_IID)
        return value.upper() if value and GUID.fullmatch(value) else None

    @contextlib.contextmanager
    def locked(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.ReleaseMutex.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateMutexW(None, False, _user_mutex_name())
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 0x80):
                raise ShellIntegrationError("Another registration action is still running.")
            acquired = True
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)

    def notify_associations_changed(self):
        shell = ctypes.WinDLL("shell32")
        shell.SHChangeNotify.argtypes = [
            ctypes.c_long, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p
        ]
        shell.SHChangeNotify.restype = None
        shell.SHChangeNotify(0x08000000, 0, None, None)
