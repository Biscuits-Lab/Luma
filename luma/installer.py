"""Narrow uninstall boundary for the per-user, versioned Windows installer.

This module never launches an older executable or accepts an arbitrary root.
The shell registrar still performs its own owner comparison and transaction.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

MAX_INSTALLED_PAYLOADS = 512
MARKER_NAME = "luma-install.json"


class InstallerError(RuntimeError):
    pass


def _plain_path(path: Path) -> bool:
    return not path.is_symlink() and not path.is_junction()


def _validated_payload(directory: Path) -> Path | None:
    if not directory.is_dir() or not _plain_path(directory):
        return None
    marker = directory / MARKER_NAME
    executable = directory / "Luma.exe"
    if not all(p.is_file() and _plain_path(p) for p in (marker, executable)):
        return None
    try:
        if marker.stat().st_size > 4096:
            return None
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(metadata, dict):
        return None
    version, digest = metadata.get("version"), metadata.get("bundle_sha256")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    payload_id = f"{version}-{digest[:24]}"
    if (metadata.get("managed_install") is not True or metadata.get("layout") != "versions-v1"
            or metadata.get("payload_id") != payload_id or directory.name != payload_id):
        return None
    return executable


def installed_executables(executable: Path) -> tuple[Path, ...]:
    """Find only marker-bearing sibling payloads of this installed executable."""
    executable = Path(executable)
    if not executable.is_absolute() or ".." in executable.parts or executable.name.casefold() != "luma.exe":
        raise InstallerError("Not a recognized installed Luma executable.")
    payload = executable.parent
    versions = payload.parent
    if versions.name != "versions" or _validated_payload(payload) is None:
        raise InstallerError("Missing or invalid Luma installer marker; registration was preserved.")
    if not all(_plain_path(p) for p in (executable, *executable.parents)):
        raise InstallerError("Installer paths must not be symlinks or junctions.")
    candidates = []
    try:
        for index, directory in enumerate(versions.iterdir()):
            if index >= MAX_INSTALLED_PAYLOADS:
                raise InstallerError("Too many installed payloads; registration was preserved.")
            candidate = _validated_payload(directory)
            if candidate is not None:
                candidates.append(candidate)
    except OSError as error:
        raise InstallerError("Could not inspect installed payloads; registration was preserved.") from error
    if executable.resolve() not in {p.resolve() for p in candidates}:
        raise InstallerError("The running installer payload could not be verified.")
    return tuple(sorted(candidates, key=lambda p: str(p).casefold()))


def uninstall_managed_installation(executable: Path) -> None:
    """Remove shell state only when owned by a payload in this installation.

Validate the complete bounded candidate set before the first registry mutation.
An unrelated portable copy is never passed to the registrar and stays registered.
Errors propagate so the uninstaller aborts before deleting application files.
"""
    candidates = installed_executables(executable)
    from .shell_integration import uninstall_if_executable
    for candidate in candidates:
        uninstall_if_executable(candidate)
