"""Opt-in GitHub updates with mode-aware apply and verified GUI relaunch.

Only this repository's stable releases are trusted. SHA256SUMS detects damaged or
mismatched downloads; it is not a substitute for publisher code signing. No
remote scripts or credentials are used. Managed updates use verified Setup in
the original root, retaining previous immutable payloads. Portable updates are
manual until their stable-entry replacement can be guaranteed safely.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import total_ordering
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from PySide6.QtCore import QObject, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QFrame, QWidget, QSizePolicy, QPushButton,
    QVBoxLayout,
)

from .i18n import tr, error_text
from .update_installation import (
    InstallationError, ManagedUpdate, apply_verified_setup, detect_installation,
)
from .update_probe import LaunchAcknowledgment, LaunchError, launch_verified_update

REPOSITORY = "Biscuits-Lab/Luma"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CDN_HOSTS = frozenset({"release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"})
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_DOWNLOAD_BYTES = 768 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 20_000
CHUNK_SIZE = 128 * 1024
NETWORK_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 15 * 60
_VERSION_RE = re.compile(r"[vV]?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?\Z")
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10)), "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}


class UpdateError(Exception):
    """An update could not be safely completed."""


class UpdateCancelled(UpdateError):
    pass


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: str = ""

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = _VERSION_RE.fullmatch(value) if isinstance(value, str) and len(value) <= 128 else None
        if not match:
            raise UpdateError(tr("The release has an unsupported version number."))
        major, minor, patch, prerelease, build = match.groups()
        pre = tuple(prerelease.split(".")) if prerelease else ()
        if any(not p or (p.isdigit() and len(p) > 1 and p[0] == "0") for p in pre):
            raise UpdateError(tr("The release has an invalid pre-release version."))
        if build and any(not p for p in build.split(".")):
            raise UpdateError(tr("The release has an invalid build version."))
        return cls(int(major), int(minor), int(patch), pre, build or "")

    def __str__(self):
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += "+" + self.build
        return text

    def _key(self):
        # A final release sorts after its pre-releases; numeric identifiers sort
        # before non-numeric identifiers, exactly as SemVer specifies.
        pre = tuple((0, int(p)) if p.isdigit() else (1, p) for p in self.prerelease)
        return self.major, self.minor, self.patch, not bool(self.prerelease), pre

    def __eq__(self, other):
        return self._key() == other._key() if isinstance(other, Version) else NotImplemented

    def __lt__(self, other):
        return self._key() < other._key() if isinstance(other, Version) else NotImplemented


def is_newer_version(candidate: str, current: str) -> bool:
    return Version.parse(candidate) > Version.parse(current)


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    digest: str | None = None


@dataclass(frozen=True)
class Release:
    version: Version
    tag: str
    url: str
    archive: ReleaseAsset | None
    checksums: ReleaseAsset | None
    setup: ReleaseAsset | None = None

    @property
    def downloadable(self):
        return self.archive is not None and self.checksums is not None


@dataclass(frozen=True)
class PreparedSetup:
    version: str
    directory: Path
    executable: Path
    sha256: str


@dataclass(frozen=True)
class InstalledUpdate:
    version: str
    directory: Path
    executable: Path


def _https_parts(url: str):
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443) or parsed.fragment:
            raise ValueError
        return parsed
    except (ValueError, TypeError) as error:
        raise UpdateError(error_text("An update URL was rejected because it is not an approved HTTPS URL.", error)) from error


def validate_asset_url(url: str, tag: str, filename: str) -> str:
    expected = f"https://github.com/{REPOSITORY}/releases/download/{quote(tag, safe='')}/{quote(filename, safe='')}"
    _https_parts(url)
    if url != expected:
        raise UpdateError(tr("An update download did not belong to the official Luma release."))
    return url


def validate_download_location(url: str, initial_url: str) -> str:
    parsed = _https_parts(url)
    if url != initial_url and parsed.hostname not in CDN_HOSTS:
        raise UpdateError(tr("The update server redirected to an unapproved download host."))
    return url


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, initial_url: str, allow_cdn: bool):
        self.initial_url = initial_url
        self.allow_cdn = allow_cdn
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.allow_cdn:
            validate_download_location(newurl, self.initial_url)
        elif newurl != self.initial_url:
            raise UpdateError(tr("The release API redirected to an unexpected address."))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_cancel(cancelled: Callable[[], bool]):
    if cancelled():
        raise UpdateCancelled(tr("Update cancelled. Your existing copy is unchanged."))


def _transfer(url: str, limit: int, sink, *, expected_size: int | None = None,
              cancelled=lambda: False, progress=lambda done, total: None,
              allow_cdn=False, opener=None) -> int:
    """Bounded streaming with validated redirects and no authentication headers."""
    _https_parts(url)
    opener = opener or build_opener(_SafeRedirectHandler(url, allow_cdn))
    request = Request(url, headers={"User-Agent": "Luma-Updater", "Accept": "application/vnd.github+json" if url == LATEST_URL else "application/octet-stream", "Accept-Encoding": "identity"})
    started = time.monotonic()
    received = 0
    try:
        _check_cancel(cancelled)
        with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
            actual_url = response.geturl()
            if allow_cdn:
                validate_download_location(actual_url, url)
            elif actual_url != url:
                raise UpdateError(tr("The release API returned an unexpected address."))
            if response.getcode() != 200:
                raise UpdateError(tr("The update server returned an unexpected response."))
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise UpdateError(tr("The update server used an unsupported content encoding."))
            length = response.headers.get("Content-Length")
            try:
                declared = int(length) if length is not None else None
            except ValueError as error:
                raise UpdateError(error_text("The update server returned an invalid download size.", error)) from error
            if declared is not None and (declared < 0 or declared > limit or (expected_size is not None and declared != expected_size)):
                raise UpdateError(tr("The download size did not match the release or exceeded the safety limit."))
            while True:
                _check_cancel(cancelled)
                if time.monotonic() - started > DOWNLOAD_TIMEOUT:
                    raise UpdateError(tr("The download timed out. Please try again."))
                chunk = response.read(min(CHUNK_SIZE, limit - received + 1))
                if not chunk:
                    break
                received += len(chunk)
                if received > limit or (expected_size is not None and received > expected_size):
                    raise UpdateError(tr("The download exceeded the expected size."))
                sink.write(chunk)
                progress(received, expected_size or declared or 0)
            if (expected_size is not None and received != expected_size) or (declared is not None and received != declared):
                raise UpdateError(tr("The download was incomplete. Please try again."))
        _check_cancel(cancelled)
        return received
    except HTTPError as error:
        if error.code == 404 and url == LATEST_URL:
            raise UpdateError(error_text("No published stable release is available yet.", error)) from error
        if error.code in (403, 429):
            raise UpdateError(error_text("GitHub temporarily limited update requests. Please try again later.", error)) from error
        raise UpdateError(error_text("The update server returned HTTP {code}. Please try again later.", error, code=error.code)) from error
    except (URLError, TimeoutError, OSError) as error:
        raise UpdateError(error_text("Unable to download the update. Check your connection and available disk space, then try again.", error)) from error


def _fetch_bytes(url: str, limit: int, **kwargs) -> bytes:
    from io import BytesIO
    output = BytesIO()
    _transfer(url, limit, output, **kwargs)
    return output.getvalue()


def parse_release(data: dict) -> Release:
    if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False:
        raise UpdateError(tr("The update service did not return a published stable release."))
    tag = data.get("tag_name")
    version = Version.parse(tag)
    if version.prerelease:
        raise UpdateError(tr("Pre-release updates are not installed by this updater."))
    release_url = f"{RELEASES_URL}/tag/{quote(tag, safe='')}"
    if data.get("html_url") != release_url:
        raise UpdateError(tr("The release link did not belong to the official Luma repository."))
    expected_names = {f"Luma-{version}-windows-x64.zip": MAX_DOWNLOAD_BYTES,
                      f"Luma-{version}-windows-x64-setup.exe": MAX_DOWNLOAD_BYTES,
                      "SHA256SUMS": MAX_MANIFEST_BYTES}
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise UpdateError(tr("The release asset list is invalid."))
    selected = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise UpdateError(tr("The release asset list is invalid."))
        name = asset.get("name")
        if not isinstance(name, str):
            raise UpdateError(tr("A release asset has an invalid filename."))
        if name not in expected_names:
            continue
        if name in selected:
            raise UpdateError(tr("The release has ambiguous duplicate update assets."))
        size = asset.get("size")
        if type(size) is not int or not 0 < size <= expected_names[name]:
            raise UpdateError(tr("A release asset has an invalid or excessive size."))
        url = validate_asset_url(asset.get("browser_download_url"), tag, name)
        digest = asset.get("digest")
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)):
            raise UpdateError(tr("A release asset has an invalid SHA-256 digest."))
        selected[name] = ReleaseAsset(name, url, size, digest[7:].lower() if digest else None)
    return Release(version, tag, release_url, selected.get(f"Luma-{version}-windows-x64.zip"), selected.get("SHA256SUMS"), selected.get(f"Luma-{version}-windows-x64-setup.exe"))


def check_for_update(*, cancelled=lambda: False, opener=None) -> Release:
    raw = _fetch_bytes(LATEST_URL, MAX_METADATA_BYTES, cancelled=cancelled, opener=opener)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise UpdateError(error_text("GitHub returned an invalid release description.", error)) from error
    return parse_release(data)


def checksum_for(manifest: bytes, filename: str) -> str:
    if len(manifest) > MAX_MANIFEST_BYTES:
        raise UpdateError(tr("The checksum manifest is too large."))
    try:
        lines = manifest.decode("utf-8-sig").splitlines()
    except UnicodeError as error:
        raise UpdateError(error_text("The checksum manifest is invalid.", error)) from error
    matches = []
    for line in lines:
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if not match:
            raise UpdateError(tr("The checksum manifest is invalid."))
        if match[2] == filename:
            matches.append(match[1].lower())
    if len(matches) != 1:
        raise UpdateError(tr("The checksum manifest must contain exactly one checksum for this update."))
    return matches[0]


def verify_checksum(path: Path, expected: str, *, cancelled=lambda: False):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            _check_cancel(cancelled)
            digest.update(chunk)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected) or digest.hexdigest() != expected.lower():
        raise UpdateError(tr("SHA-256 verification failed. The update was discarded; your existing copy is unchanged."))


def _safe_member_name(info: zipfile.ZipInfo) -> tuple[str, ...]:
    # Inspect orig_filename as ZipInfo truncates filename at an embedded NUL.
    name = info.orig_filename
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise UpdateError(tr("The update archive contains an unsafe path."))
    path = name[:-1] if name.endswith("/") else name
    parts = tuple(path.split("/"))
    if len(parts) > 32 or len(path) > 220:
        raise UpdateError(tr("The update archive contains an excessive path."))
    for part in parts:
        if not part or part in (".", "..") or part[-1] in (" ", ".") or any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            raise UpdateError(tr("The update archive contains an unsafe Windows path."))
        if part.split(".", 1)[0].rstrip(" .").upper() in _RESERVED_NAMES:
            raise UpdateError(tr("The update archive contains a reserved Windows filename."))
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR) or stat.S_ISLNK(mode):
        raise UpdateError(tr("The update archive contains a link or special file."))
    # DOS reparse points can also represent links on Windows.
    if info.external_attr & 0x400:
        raise UpdateError(tr("The update archive contains a reparse point."))
    if info.flag_bits & 0x1:
        raise UpdateError(tr("Encrypted update archives are not supported."))
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise UpdateError(tr("The update archive uses an unsupported compression method."))
    return parts


def safe_extract_zip(archive: Path, destination: Path, *, cancelled=lambda: False) -> Path:
    """Extract into an empty, privately created staging directory, never in place."""
    if destination.exists() and (destination.is_symlink() or any(destination.iterdir())):
        raise UpdateError(tr("The update staging directory is not empty."))
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if not members or len(members) > MAX_MEMBERS:
                raise UpdateError(tr("The update archive contains an invalid number of files."))
            checked = []
            seen = {}
            total = 0
            for info in members:
                _check_cancel(cancelled)
                parts = _safe_member_name(info)
                key = "/".join(parts).casefold()
                if key in seen:
                    raise UpdateError(tr("The update archive contains duplicate Windows paths."))
                seen[key] = info.is_dir()
                if info.file_size < 0 or info.file_size > MAX_FILE_BYTES:
                    raise UpdateError(tr("The update archive contains an oversized file."))
                total += info.file_size
                if total > MAX_EXTRACTED_BYTES:
                    raise UpdateError(tr("The update archive exceeds the extracted size limit."))
                checked.append((info, parts))
            for key in seen:
                parent = PurePosixPath(key).parent
                while str(parent) != ".":
                    if str(parent) in seen and not seen[str(parent)]:
                        raise UpdateError(tr("The update archive contains conflicting paths."))
                    parent = parent.parent
            if shutil.disk_usage(root).free < total + 64 * 1024 * 1024:
                raise UpdateError(tr("There is not enough free disk space to install this update."))
            for info, parts in checked:
                _check_cancel(cancelled)
                target = root.joinpath(*parts)
                if not target.resolve().is_relative_to(root):
                    raise UpdateError(tr("The update archive tried to leave its staging directory."))
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with bundle.open(info) as source, target.open("xb") as output:
                    while chunk := source.read(CHUNK_SIZE):
                        _check_cancel(cancelled)
                        written += len(chunk)
                        if written > info.file_size or written > MAX_FILE_BYTES:
                            raise UpdateError(tr("An extracted file exceeded its expected size."))
                        output.write(chunk)
                if written != info.file_size:
                    raise UpdateError(tr("An extracted update file was incomplete."))
        candidates = [root / "Luma.exe", root / "Luma" / "Luma.exe"]
        executable = [path for path in candidates if path.is_file() and not path.is_symlink()]
        if len(executable) != 1:
            raise UpdateError(tr("The update archive does not contain one expected Luma.exe."))
        with executable[0].open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise UpdateError(tr("The update does not contain a Windows executable."))
        return executable[0]
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as error:
        raise UpdateError(error_text("The update ZIP archive is invalid or damaged.", error)) from error


def default_install_root() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    if not location:
        raise UpdateError(tr("A writable local application-data directory is unavailable."))
    return Path(location) / "updates"


def install_release(release: Release, install_root: Path, *, cancelled=lambda: False,
                    progress=lambda done, total: None, status=lambda text: None,
                    opener=None) -> InstalledUpdate:
    """Download, validate and atomically commit a new side-by-side version.

    Never launches the program or changes/deletes the user's existing app.
    Incomplete downloads and failed extraction are automatically removed.
    """
    if not release.downloadable:
        raise UpdateError(tr("This release does not yet have a Windows package and SHA256SUMS."))
    archive, checksums = release.archive, release.checksums
    if str(Version.parse(release.tag)) != str(release.version):
        raise UpdateError(tr("The release tag and version do not match."))
    # Revalidate even callers that construct Release directly cannot supply a URL
    # outside the official repository or an unexpected package name.
    if release.version.prerelease or archive.name != f"Luma-{release.version}-windows-x64.zip" or checksums.name != "SHA256SUMS":
        raise UpdateError(tr("This is not a supported stable Windows update."))
    for asset, limit in ((archive, MAX_DOWNLOAD_BYTES), (checksums, MAX_MANIFEST_BYTES)):
        validate_asset_url(asset.url, release.tag, asset.name)
        if type(asset.size) is not int or not 0 < asset.size <= limit:
            raise UpdateError(tr("A release asset exceeds the download safety limit."))
    install_root = Path(install_root)
    if install_root.is_symlink():
        raise UpdateError(tr("The update folder cannot be a symbolic link."))
    install_root.mkdir(parents=True, exist_ok=True)
    install_root = install_root.resolve()
    if shutil.disk_usage(install_root).free < archive.size + 64 * 1024 * 1024:
        raise UpdateError(tr("There is not enough free disk space to download this update."))
    try:
        with tempfile.TemporaryDirectory(prefix=".staging-", dir=install_root) as temp:
            staging = Path(temp)
            status(tr("Downloading checksums…"))
            manifest = _fetch_bytes(checksums.url, MAX_MANIFEST_BYTES, expected_size=checksums.size, allow_cdn=True, cancelled=cancelled, opener=opener)
            expected = checksum_for(manifest, archive.name)
            if checksums.digest and hashlib.sha256(manifest).hexdigest() != checksums.digest:
                raise UpdateError(tr("The checksum manifest does not match GitHub's SHA-256 digest."))
            if archive.digest and archive.digest != expected:
                raise UpdateError(tr("The release checksums disagree. The update was not installed."))
            package = staging / "download.zip"
            status(tr("Downloading update…"))
            with package.open("xb") as stream:
                _transfer(archive.url, MAX_DOWNLOAD_BYTES, stream, expected_size=archive.size, allow_cdn=True, cancelled=cancelled, progress=progress, opener=opener)
            status(tr("Verifying SHA-256…"))
            verify_checksum(package, expected, cancelled=cancelled)
            status(tr("Installing a separate copy…"))
            executable = safe_extract_zip(package, staging / "app", cancelled=cancelled)
            relative_executable = executable.relative_to(staging / "app")
            _check_cancel(cancelled)
            # Unique versioned destinations avoid replacing a possibly running
            # installation when two windows/processes install the same version.
            name = f"Luma-{release.version}-{staging.name.removeprefix('.staging-')}"
            destination = install_root / name
            if destination.exists():
                raise UpdateError(tr("The update destination already exists. Please try again."))
            (staging / "app").rename(destination)
            return InstalledUpdate(str(release.version), destination, destination / relative_executable)
    except OSError as error:
        raise UpdateError(error_text("The update could not be saved. Check disk space and folder permissions. Your existing copy is unchanged.", error)) from error


def download_setup(release: Release, cache_root: Path, *, cancelled=lambda: False,
                   progress=lambda done, total: None, status=lambda text: None,
                   opener=None) -> PreparedSetup:
    """Stage the official Setup; this function never executes downloaded bytes."""
    setup, checksums = release.setup, release.checksums
    if setup is None or checksums is None:
        raise UpdateError(tr("This release does not have an installer and SHA256SUMS yet."))
    if (str(Version.parse(release.tag)) != str(release.version) or release.version.prerelease
            or setup.name != f"Luma-{release.version}-windows-x64-setup.exe"
            or checksums.name != "SHA256SUMS"):
        raise UpdateError(tr("This is not a supported stable Windows update."))
    for asset, limit in ((setup, MAX_DOWNLOAD_BYTES), (checksums, MAX_MANIFEST_BYTES)):
        validate_asset_url(asset.url, release.tag, asset.name)
        if type(asset.size) is not int or not 0 < asset.size <= limit:
            raise UpdateError(tr("A release asset exceeds the download safety limit."))
    cache_root = Path(cache_root).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (cache_root, *cache_root.parents)):
        raise UpdateError(tr("The update folder cannot be a symbolic link."))
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_root = cache_root.resolve(strict=True)
    if shutil.disk_usage(cache_root).free < setup.size + 64 * 1024 * 1024:
        raise UpdateError(tr("There is not enough free disk space to download this update."))
    try:
        with tempfile.TemporaryDirectory(prefix=".setup-staging-", dir=cache_root) as temp:
            staging = Path(temp)
            status(tr("Downloading checksums…"))
            manifest = _fetch_bytes(checksums.url, MAX_MANIFEST_BYTES, expected_size=checksums.size,
                                    allow_cdn=True, cancelled=cancelled, opener=opener)
            expected = checksum_for(manifest, setup.name)
            if checksums.digest and hashlib.sha256(manifest).hexdigest() != checksums.digest:
                raise UpdateError(tr("The checksum manifest does not match GitHub's SHA-256 digest."))
            if setup.digest and setup.digest != expected:
                raise UpdateError(tr("The release checksums disagree. The update was not installed."))
            package = staging / setup.name
            status(tr("Downloading update…"))
            with package.open("xb") as stream:
                _transfer(setup.url, MAX_DOWNLOAD_BYTES, stream, expected_size=setup.size,
                          allow_cdn=True, cancelled=cancelled, progress=progress, opener=opener)
            status(tr("Verifying SHA-256…"))
            verify_checksum(package, expected, cancelled=cancelled)
            with package.open("rb") as stream:
                if stream.read(2) != b"MZ":
                    raise UpdateError(tr("The verified installer is not a Windows executable."))
            _check_cancel(cancelled)
            # Retain a uniquely named staging parent for an installer that can
            # outlive its Settings dialog. Never remove it on uncertain exit.
            destination = cache_root / ("Setup-" + str(release.version) + "-" + staging.name.removeprefix(".setup-staging-"))
            staging.rename(destination)
            return PreparedSetup(str(release.version), destination, destination / setup.name, expected)
    except OSError as error:
        raise UpdateError(error_text("The update could not be saved. Check disk space and folder permissions. Your existing copy is unchanged.", error)) from error


class _UpdateSignals(QObject):
    completed = Signal(int, object)
    failed = Signal(int, str)
    progress = Signal(int, int, int)
    status = Signal(int, str)


class UpdateDialog(QDialog):
    """User-initiated only: opening Settings does not make any network request."""
    def __init__(self, parent, current_version: str):
        super().__init__(parent)
        self.setWindowTitle(tr("Luma · Settings / Updates"))
        self.setMinimumWidth(480)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.current_version = current_version
        self.release = None
        self.installed = None
        self.installation = None
        self._serial = 0
        self._busy = False
        self._cancel = threading.Event()
        QApplication.instance().aboutToQuit.connect(self._cancel_work)
        self._signals = _UpdateSignals(self)
        self._signals.completed.connect(self._completed)
        self._signals.failed.connect(self._failed)
        self._signals.progress.connect(self._progress)
        self._signals.status.connect(self._status)
        from .settings_widgets import (
            ActivityStatusLabel, DetailsPanel, DisplaySettingsPanel, SettingsCard,
            SettingsCloseButton, badge, plain_label, settings_stylesheet,
        )
        self.setObjectName("luma_settings")
        self.setStyleSheet(settings_stylesheet())
        # Qt does not constrain an explicit resize to the usable desktop. At
        # high DPI the screen's geometry is already in logical pixels; leave
        # space for native borders/titlebar and let the body scroll instead.
        screen = parent.screen() if parent is not None else self.screen()
        width, height = 620, 310
        if screen is not None:
            available = screen.availableGeometry()
            width = max(self.minimumWidth(), min(width, available.width() - 32))
            height = max(240, min(height, available.height() - 48))
        self.resize(width, height)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 18)
        outer.setSpacing(12)
        header = QHBoxLayout()
        title = plain_label(tr("Settings"))
        title.setObjectName("settings_title")
        header.addWidget(title)
        header.addStretch()
        self.close_button = SettingsCloseButton()
        self.close_button.setAccessibleName(tr("Close"))
        self.close_button.setToolTip(tr("Close"))
        self.close_button.clicked.connect(self.reject)
        header.addWidget(self.close_button)
        outer.addLayout(header)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 5, 0)
        layout.setSpacing(12)
        # Settings can also be opened by tests/standalone tools without a viewer.
        # Only offer working controls backed by this actual viewer window.
        self.display_panel = None
        if hasattr(parent, "background"):
            self.display_panel = DisplaySettingsPanel(parent)
            layout.addWidget(self.display_panel)
        self.check_button = QPushButton(tr("Check for updates"))
        self.check_button.setObjectName("check_updates")
        self.check_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.check_button.clicked.connect(self.check)
        header_actions = QWidget()
        header_actions.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        header_row = QHBoxLayout(header_actions)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        self.version_badge = badge(current_version)
        header_row.addWidget(self.version_badge)
        header_row.addWidget(self.check_button)
        self.update_card = SettingsCard(tr("Version & updates"), "update", accessory=header_actions)
        update_layout = self.update_card.body
        self.status_label = ActivityStatusLabel()
        self.status_label.setObjectName("update_status")
        update_layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setAccessibleName(tr("Update progress"))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.hide()
        update_layout.addWidget(self.progress_bar)
        self.download_button = QPushButton(tr("Download and install update"))
        self.download_button.setObjectName("download_update")
        self.download_button.clicked.connect(self.download)
        self.download_button.hide()
        self.launch_button = QPushButton(tr("Launch new version and close Luma"))
        self.launch_button.setObjectName("launch_update")
        self.launch_button.clicked.connect(self.launch)
        self.launch_button.hide()
        self.release_button = QPushButton(tr("View release on GitHub"))
        self.release_button.clicked.connect(self.open_release)
        self.release_button.hide()
        for widget in (self.download_button, self.launch_button, self.release_button):
            update_layout.addWidget(widget)
        self.update_details = DetailsPanel(tr("About updates"))
        self.description = plain_label(tr("Check the official GitHub repository for a stable Windows update.\nNo account or sign-in is required."), muted=True, wrap=True)
        self.update_details.body.addWidget(self.description)
        update_layout.addWidget(self.update_details)
        layout.addWidget(self.update_card)
        layout.addStretch(1)
        # Enter must not initiate an update or download accidentally.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.close_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _start(self, function):
        if self._busy:
            return
        self._busy = True
        self._serial += 1
        serial = self._serial
        self._cancel = threading.Event()
        cancelled, signals = self._cancel.is_set, self._signals
        self.check_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.launch_button.setEnabled(False)
        self.close_button.setAccessibleName(tr("Cancel"))
        self.close_button.setToolTip(tr("Cancel"))
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()

        def emit(signal, *values):
            try:
                signal.emit(serial, *values)
            except RuntimeError:
                pass  # Settings may close while native Setup is still running.

        def run():
            try:
                result = function(cancelled, lambda done, total: emit(signals.progress, done, total), lambda message: emit(signals.status, tr(message)))
                if not cancelled():
                    emit(signals.completed, result)
            except Exception as error:
                if not cancelled():
                    try:
                        emit(signals.failed, str(error) if isinstance(error, UpdateError) else error_text("The update could not be completed. Your current Luma stays open.", error))
                    except RuntimeError:
                        pass  # The dialog/application was closed during I/O.

        threading.Thread(target=run, daemon=True, name="Luma updates").start()

    def check(self):
        if self._busy:
            return
        self.release = self.installed = None
        self.download_button.hide()
        self.launch_button.hide()
        self.release_button.hide()
        self.status_label.setText(tr("Checking for a stable release…"))
        self._start(lambda cancelled, progress, status: check_for_update(cancelled=cancelled))

    def download(self):
        if self._busy or not self.release or sys.platform != "win32":
            return
        release = self.release
        try:
            installation = detect_installation()
        except InstallationError as error:
            self.status_label.setText(error_text("The installation could not be verified. Please run Setup again in its original folder.", error))
            return
        self.installation = installation
        if installation.mode == "managed":
            def apply(cancelled, progress, status):
                prepared = download_setup(release, default_install_root(), cancelled=cancelled,
                                          progress=progress, status=status)
                return apply_verified_setup(prepared.executable, prepared.sha256, prepared.version,
                                            installation, cancelled=cancelled, status=status)
            self._start(apply)
        elif installation.mode == "portable":
            self.download_button.hide()
            self.status_label.setText(tr("Portable copies require a manual update. Download Setup to use managed updates, or extract the new ZIP into a new folder and update your shortcut. The current portable folder is unchanged."))
        else:
            self.status_label.setText(tr("Automatic installation requires a packaged Windows Luma. Download Setup from the release page."))

    def _finish(self):
        self._busy = False
        self.check_button.setEnabled(True)
        self.download_button.setEnabled(True)
        self.launch_button.setEnabled(True)
        self.progress_bar.hide()
        self.close_button.setAccessibleName(tr("Close"))
        self.close_button.setToolTip(tr("Close"))

    def _completed(self, serial, result):
        if serial != self._serial or self._cancel.is_set():
            return
        self._finish()
        if isinstance(result, Release):
            self.release = result
            self.release_button.show()
            try:
                newer = result.version > Version.parse(self.current_version)
            except UpdateError as error:
                self.status_label.setText(str(error))
                return
            if not newer:
                self.status_label.setText(tr("You're up to date. Latest stable release: {version}", version=result.version))
            elif sys.platform != "win32":
                self.status_label.setText(tr("Luma {version} is available.\nDownload and installation from this window require Windows x64.", version=result.version))
            else:
                try:
                    self.installation = detect_installation()
                except InstallationError as error:
                    self.status_label.setText(error_text("The installation could not be verified. Please run Setup again in its original folder.", error))
                    return
                if self.installation.mode not in ("managed", "portable"):
                    self.status_label.setText(tr("Automatic installation requires a packaged Windows Luma. Download Setup from the release page."))
                    return
                if self.installation.mode == "portable":
                    self.status_label.setText(tr("Portable copies require a manual update. Download Setup to use managed updates, or extract the new ZIP into a new folder and update your shortcut. The current portable folder is unchanged."))
                    return
                asset = result.setup
                if asset is None or result.checksums is None:
                    self.status_label.setText(tr("The package for this installation is not published yet. Please check again later."))
                    return
                size = asset.size / (1024 * 1024)
                self.status_label.setText(tr("Luma {version} is available ({size:.1f} MB).\nAfter SHA-256 verification, Setup updates this installation and its shortcuts. Previous payloads remain available. Your current app stays open until the new version confirms startup.", version=result.version, size=size))
                self.download_button.show()
        elif isinstance(result, ManagedUpdate):
            self.installed = result
            self.download_button.hide()
            self.launch_button.show()
            self.status_label.setText(tr("Luma {version} is installed in your original installation.\n{executable}\nRestart to verify and open the new version.", version=result.version, executable=result.executable))
        elif isinstance(result, LaunchAcknowledgment):
            # A successful process creation or an installer exit is insufficient.
            # This identity comes from the newly painted, running frozen app.
            self.accept()
            QApplication.instance().quit()

    def _failed(self, serial, message):
        if serial != self._serial or self._cancel.is_set():
            return
        self._finish()
        self.status_label.setText(message)

    def _progress(self, serial, done, total):
        if serial != self._serial or self._cancel.is_set():
            return
        if total:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(min(100, int(done * 100 / total)))
        else:
            self.progress_bar.setRange(0, 0)

    def _status(self, serial, message):
        if serial == self._serial and not self._cancel.is_set():
            self.status_label.setText(message)
            if message == tr("Setup is running in its own window. You can cancel there; your current Luma stays open."):
                self.close_button.setAccessibleName(tr("Close"))
                self.close_button.setToolTip(tr("Close settings. Setup continues in its own window."))

    def open_release(self):
        if self.release:
            QDesktopServices.openUrl(QUrl(self.release.url))

    def launch(self):
        if not isinstance(self.installed, ManagedUpdate) or self._busy or sys.platform != "win32":
            return
        executable = self.installed.executable
        if executable.is_symlink() or not executable.is_file() or not executable.resolve().is_relative_to(self.installed.directory.resolve()):
            self.status_label.setText(tr("The installed application is missing or was moved. Please install the update again."))
            return
        self.status_label.setText(tr("Starting and verifying the new version…"))
        self._start(lambda cancelled, progress, status: launch_verified_update(
            executable, self.installed.version, cancelled=cancelled))

    def _cancel_work(self):
        self._cancel.set()
        self._serial += 1

    def done(self, result):
        self._cancel_work()
        super().done(result)

    def closeEvent(self, event):
        self._cancel_work()
        super().closeEvent(event)


def open_update_dialog(parent, current_version: str) -> UpdateDialog:
    """Open one modeless Settings dialog per viewer; no network until Check."""
    existing = getattr(parent, "_luma_update_dialog", None)
    if existing is not None:
        existing.raise_()
        existing.activateWindow()
        return existing
    dialog = UpdateDialog(parent, current_version)
    parent._luma_update_dialog = dialog
    dialog.finished.connect(lambda result: setattr(parent, "_luma_update_dialog", None))
    dialog.show()
    return dialog
