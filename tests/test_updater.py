from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
import zipfile

import pytest
from PySide6.QtWidgets import QWidget

from luma import updater


def zip_bytes(entries=None):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in entries or [("Luma/Luma.exe", b"MZfake-executable"), ("Luma/_internal/runtime.dll", b"runtime")]:
            # ZipInfo normalizes Windows separators in its constructor. Preserve
            # deliberately malformed names so security fixtures are identical
            # on Windows and POSIX, rather than silently testing a safe path.
            info = zipfile.ZipInfo(name)
            info.filename = info.orig_filename = name
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["Luma\\escape.exe", "Luma//file.dll", "Luma/./file.dll", "/absolute.exe"])
def test_zip_fixture_preserves_untrusted_names(name):
    payload = zip_bytes([(name, b"fixture")])
    # The same raw name must appear in both the local and central ZIP headers.
    assert payload.count(name.encode("utf-8")) == 2
    with zipfile.ZipFile(BytesIO(payload)) as bundle:
        assert bundle.infolist()[0].orig_filename == name


def release_data(package=None):
    package = package if package is not None else zip_bytes()
    name = "Luma-0.1.1-windows-x64.zip"
    manifest = (hashlib.sha256(package).hexdigest() + "  " + name + "\n").encode()
    base = "https://github.com/Biscuits-Lab/Luma/releases/download/v0.1.1/"
    data = {"tag_name": "v0.1.1", "html_url": updater.RELEASES_URL + "/tag/v0.1.1", "draft": False, "prerelease": False,
            "assets": [{"name": name, "browser_download_url": base + name, "size": len(package)},
                       {"name": "SHA256SUMS", "browser_download_url": base + "SHA256SUMS", "size": len(manifest)}]}
    return data, package, manifest


class Response(BytesIO):
    def __init__(self, data, url, headers=None, status=200):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data))} if headers is None else headers
        self.status = status

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status


class Opener:
    def __init__(self, bodies):
        self.bodies = bodies
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        body = self.bodies[request.full_url]
        return body if isinstance(body, Response) else Response(body, request.full_url)


def wait_for(qapp, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    qapp.processEvents()
    assert predicate()


@pytest.mark.parametrize(("candidate", "current", "expected"), [
    ("v0.1.1", "0.1.0", True), ("0.10.0", "v0.9.9", True),
    ("v0.1.0", "0.1.0", False), ("0.1.0+build2", "0.1.0+build1", False),
    ("1.0.0", "1.0.0-rc.1", True), ("1.0.0-rc.10", "1.0.0-rc.2", True),
    ("1.0.0-alpha.1", "1.0.0-alpha", True), ("1.0.0-9", "1.0.0-beta", False),
    ("0.1.0", "0.2.0", False),
])
def test_version_comparison(candidate, current, expected):
    assert updater.is_newer_version(candidate, current) is expected


@pytest.mark.parametrize("version", ["latest", "0.1", "01.1.2", "1.2.3-01", "1.2.3-a..b", "1.2.3+..", "1.2.3/evil", None])
def test_invalid_versions_rejected(version):
    with pytest.raises(updater.UpdateError):
        updater.Version.parse(version)


def test_latest_uses_anonymous_official_api_and_stable_release():
    data, _, _ = release_data()
    opener = Opener({updater.LATEST_URL: json.dumps(data).encode()})
    release = updater.check_for_update(opener=opener)
    request, timeout = opener.requests[0]
    assert request.full_url == updater.LATEST_URL
    assert "Authorization" not in request.headers
    assert timeout == updater.NETWORK_TIMEOUT
    assert release.downloadable
    assert str(release.version) == "0.1.1"
    assert release.url == updater.RELEASES_URL + "/tag/v0.1.1"


@pytest.mark.parametrize("change", [
    {"draft": True}, {"prerelease": True}, {"draft": None},
    {"tag_name": "v0.1.1-rc.1"}, {"html_url": "https://github.com/attacker/Luma/releases/tag/v0.1.1"},
])
def test_unpublished_prerelease_and_unofficial_release_rejected(change):
    data, _, _ = release_data()
    data.update(change)
    with pytest.raises(updater.UpdateError):
        updater.parse_release(data)


@pytest.mark.parametrize("url", [
    "http://github.com/Biscuits-Lab/Luma/releases/download/v0.1.1/Luma-0.1.1-windows-x64.zip",
    "https://github.com.evil.test/Biscuits-Lab/Luma/releases/download/v0.1.1/Luma-0.1.1-windows-x64.zip",
    "https://github.com/another/Luma/releases/download/v0.1.1/Luma-0.1.1-windows-x64.zip",
    "https://github.com/Biscuits-Lab/Luma/releases/download/v0.1.0/Luma-0.1.1-windows-x64.zip",
    "https://user:password@github.com/Biscuits-Lab/Luma/releases/download/v0.1.1/Luma-0.1.1-windows-x64.zip",
    "https://github.com/Biscuits-Lab/Luma/releases/download/v0.1.1/Luma-0.1.1-windows-x64.zip?redirect=evil",
])
def test_invalid_asset_origin_rejected(url):
    data, _, _ = release_data()
    data["assets"][0]["browser_download_url"] = url
    with pytest.raises(updater.UpdateError):
        updater.parse_release(data)


def test_missing_asset_is_checkable_but_not_installable(tmp_path):
    data, _, _ = release_data()
    data["assets"].pop()
    release = updater.parse_release(data)
    assert not release.downloadable
    with pytest.raises(updater.UpdateError, match="SHA256SUMS"):
        updater.install_release(release, tmp_path)


@pytest.mark.parametrize("value", [-1, 0, True, "123", updater.MAX_DOWNLOAD_BYTES + 1])
def test_invalid_asset_size(value):
    data, _, _ = release_data()
    data["assets"][0]["size"] = value
    with pytest.raises(updater.UpdateError):
        updater.parse_release(data)


def test_duplicate_asset_rejected():
    data, _, _ = release_data()
    data["assets"].append(deepcopy(data["assets"][0]))
    with pytest.raises(updater.UpdateError, match="duplicate"):
        updater.parse_release(data)


def test_only_exact_github_asset_cdn_redirects_allowed():
    initial = release_data()[0]["assets"][0]["browser_download_url"]
    for host in updater.CDN_HOSTS:
        url = f"https://{host}/some-asset?signed=query"
        assert updater.validate_download_location(url, initial) == url
    for url in ["http://release-assets.githubusercontent.com/file", "https://release-assets.githubusercontent.com.evil.test/file", "https://github.com/other/repo/file", "https://objects.githubusercontent.com:444/file", "https://user@objects.githubusercontent.com/file", "https://raw.githubusercontent.com/file"]:
        with pytest.raises(updater.UpdateError):
            updater.validate_download_location(url, initial)
    handler = updater._SafeRedirectHandler(initial, True)
    with pytest.raises(updater.UpdateError):
        handler.redirect_request(None, None, 302, "", {}, "https://attacker.test/file")
    handler = updater._SafeRedirectHandler(updater.LATEST_URL, False)
    with pytest.raises(updater.UpdateError):
        handler.redirect_request(None, None, 302, "", {}, "https://objects.githubusercontent.com/file")


def test_final_download_url_is_revalidated():
    initial = release_data()[0]["assets"][0]["browser_download_url"]
    opener = Opener({initial: Response(b"evil", "https://attacker.test/app")})
    with pytest.raises(updater.UpdateError, match="unapproved"):
        updater._fetch_bytes(initial, 10, allow_cdn=True, opener=opener)


@pytest.mark.parametrize(("headers", "body", "size"), [
    ({"Content-Length": "100"}, b"x", None),
    ({"Content-Length": "garbage"}, b"x", None),
    ({"Content-Length": "2"}, b"x", None),
    ({}, b"01234567890", None),
    ({}, b"x", 2),
    ({"Content-Encoding": "gzip"}, b"x", None),
])
def test_streaming_caps_and_truncation(headers, body, size):
    opener = Opener({updater.LATEST_URL: Response(body, updater.LATEST_URL, headers)})
    with pytest.raises(updater.UpdateError):
        updater._fetch_bytes(updater.LATEST_URL, 10, opener=opener, expected_size=size)


@pytest.mark.parametrize(("status", "message"), [(404, "No published stable release"), (403, "limited"), (429, "limited"), (500, "HTTP 500")])
def test_actionable_http_errors(status, message):
    class FailedOpener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, status, "failure", {}, None)
    with pytest.raises(updater.UpdateError, match=message):
        updater.check_for_update(opener=FailedOpener())


def test_checksum_manifest_and_corruption(tmp_path):
    content = b"test archive"
    path = tmp_path / "archive.zip"
    path.write_bytes(content)
    checksum = hashlib.sha256(content).hexdigest()
    manifest = f"{checksum}  Luma.zip\n{'a' * 64} *source.zip\n".encode()
    assert updater.checksum_for(manifest, "Luma.zip") == checksum
    updater.verify_checksum(path, checksum)
    path.write_bytes(b"damaged")
    with pytest.raises(updater.UpdateError, match="verification failed"):
        updater.verify_checksum(path, checksum)
    for bad in [b"garbage", manifest + manifest, b"", f"{'a' * 64}  other.zip\n".encode()]:
        with pytest.raises(updater.UpdateError):
            updater.checksum_for(bad, "Luma.zip")


@pytest.mark.parametrize("name", [
    "../escape.exe", "/absolute.exe", "C:/escape.exe", "Luma/../../escape.exe",
    "Luma\\escape.exe", "Luma//file.dll", "Luma/./file.dll", "Luma/file.dll:stream",
    "Luma/CON", "Luma/NUL.txt", "Luma/com1.dll", "Luma/LPT².txt", "Luma/trailing.", "Luma/trailing ",
    "Luma/bad?.dll", "Luma/bad\x01.dll", "Luma/" + "x" * 221,
])
def test_unsafe_zip_paths_rejected(tmp_path, name):
    path = tmp_path / "bad.zip"
    path.write_bytes(zip_bytes([("Luma/Luma.exe", b"MZexe"), (name, b"bad")]))
    with pytest.raises(updater.UpdateError):
        updater.safe_extract_zip(path, tmp_path / "extract")
    assert not (tmp_path / "escape.exe").exists()


def test_embedded_nul_and_symlink_rejected():
    info = zipfile.ZipInfo("safe\x00evil")
    with pytest.raises(updater.UpdateError):
        updater._safe_member_name(info)
    for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR):
        info = zipfile.ZipInfo("Luma/link")
        info.create_system = 3
        info.external_attr = (kind | 0o777) << 16
        with pytest.raises(updater.UpdateError, match="special file"):
            updater._safe_member_name(info)


@pytest.mark.parametrize("entries", [
    [("Luma/Luma.exe", b"MZexe"), ("Luma/LUMA.exe", b"MZother")],
    [("Luma/Luma.exe", b"MZexe"), ("Luma/data", b"file"), ("Luma/DATA/child", b"child")],
    [("Luma.exe", b"MZexe"), ("Luma/Luma.exe", b"MZexe")],
    [("nested/other.exe", b"MZexe")],
    [("Luma.exe", b"not executable")],
])
def test_ambiguous_and_invalid_archives_rejected(tmp_path, entries):
    path = tmp_path / "archive.zip"
    path.write_bytes(zip_bytes(entries))
    with pytest.raises(updater.UpdateError):
        updater.safe_extract_zip(path, tmp_path / "extract")


def test_zip_symlink_member_rejected(tmp_path):
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("Luma/Luma.exe", b"MZexe")
        link = zipfile.ZipInfo("Luma/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(link, "../../outside")
    with pytest.raises(updater.UpdateError):
        updater.safe_extract_zip(path, tmp_path / "extract")


def test_zip_size_and_count_limits(tmp_path, monkeypatch):
    path = tmp_path / "archive.zip"
    path.write_bytes(zip_bytes())
    for constant, limit in [("MAX_FILE_BYTES", 1), ("MAX_EXTRACTED_BYTES", 1), ("MAX_MEMBERS", 1)]:
        with monkeypatch.context() as patch:
            patch.setattr(updater, constant, limit)
            with pytest.raises(updater.UpdateError):
                updater.safe_extract_zip(path, tmp_path / "extract")


@pytest.mark.parametrize("nested", [True, False])
def test_verified_install_is_side_by_side_and_never_launches(tmp_path, monkeypatch, nested):
    root = "Luma/" if nested else ""
    data, package, manifest = release_data(zip_bytes([(root + "Luma.exe", b"MZexe"), (root + "_internal/library.dll", b"library")]))
    release = updater.parse_release(data)
    opener = Opener({release.archive.url: package, release.checksums.url: manifest})
    old = tmp_path / "old" / "Luma.exe"
    old.parent.mkdir()
    old.write_bytes(b"old")
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not automatically launch"))
    stages, progress = [], []
    installed = updater.install_release(release, tmp_path / "updates", opener=opener, status=stages.append, progress=lambda a, b: progress.append((a, b)))
    assert installed.executable.read_bytes() == b"MZexe"
    assert installed.executable.is_relative_to(tmp_path / "updates")
    assert (installed.executable.parent / "_internal" / "library.dll").read_bytes() == b"library"
    assert old.read_bytes() == b"old"
    assert installed.version == "0.1.1"
    assert len(list((tmp_path / "updates").iterdir())) == 1
    assert progress[-1] == (len(package), len(package))
    assert "Verifying SHA-256…" in stages


def test_failed_checksum_leaves_no_installed_or_staged_files(tmp_path):
    data, package, manifest = release_data()
    release = updater.parse_release(data)
    damaged = b"X" * len(package)
    opener = Opener({release.archive.url: damaged, release.checksums.url: manifest})
    with pytest.raises(updater.UpdateError, match="verification failed"):
        updater.install_release(release, tmp_path, opener=opener)
    assert list(tmp_path.iterdir()) == []


def test_matching_digest_and_manifest_required(tmp_path):
    data, package, manifest = release_data()
    data["assets"][0]["digest"] = "sha256:" + "f" * 64
    release = updater.parse_release(data)
    opener = Opener({release.checksums.url: manifest})
    with pytest.raises(updater.UpdateError, match="disagree"):
        updater.install_release(release, tmp_path, opener=opener)
    assert list(tmp_path.iterdir()) == []


def test_cancelled_download_cleans_staging_and_does_not_install(tmp_path):
    data, package, manifest = release_data()
    release = updater.parse_release(data)
    opener = Opener({release.archive.url: package, release.checksums.url: manifest})
    cancel = threading.Event()
    with pytest.raises(updater.UpdateCancelled):
        updater.install_release(release, tmp_path, opener=opener, cancelled=cancel.is_set, progress=lambda *args: cancel.set())
    assert list(tmp_path.iterdir()) == []


def test_dialog_open_is_offline_check_is_async_and_can_cancel(qapp, monkeypatch):
    calls = []
    started, stop = threading.Event(), threading.Event()

    def fake_check(*, cancelled):
        calls.append(threading.current_thread())
        started.set()
        while not cancelled() and not stop.wait(.01):
            pass
        raise updater.UpdateCancelled("cancelled")

    monkeypatch.setattr(updater, "check_for_update", fake_check)
    parent = QWidget()
    dialog = updater.open_update_dialog(parent, "0.1.0")
    assert calls == []
    assert updater.open_update_dialog(parent, "0.1.0") is dialog
    dialog.check_button.click()
    wait_for(qapp, started.is_set)
    assert not dialog.check_button.isEnabled()
    dialog.check()
    assert len(calls) == 1
    assert calls[0] is not threading.main_thread()
    dialog.reject()
    assert dialog._cancel.is_set()
    assert parent._luma_update_dialog is None
    stop.set()
    qapp.processEvents()
    parent.close()


def test_non_windows_can_check_without_install(qapp, monkeypatch):
    release = updater.parse_release(release_data()[0])
    monkeypatch.setattr(updater, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(updater, "check_for_update", lambda **kwargs: release)
    parent = QWidget()
    dialog = updater.open_update_dialog(parent, "0.1.0")
    dialog.check()
    wait_for(qapp, lambda: not dialog._busy)
    assert "require Windows x64" in dialog.status_label.text()
    assert dialog.download_button.isHidden()
    dialog.reject()
    qapp.processEvents()


def test_windows_dialog_download_requires_click_then_separate_launch(qapp, tmp_path, monkeypatch):
    release = updater.parse_release(release_data()[0])
    executable = tmp_path / "Luma.exe"
    executable.write_bytes(b"MZexe")
    installed = updater.InstalledUpdate("0.1.1", tmp_path, executable)
    install_calls = []
    monkeypatch.setattr(updater, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(updater, "check_for_update", lambda **kwargs: release)
    monkeypatch.setattr(updater, "install_release", lambda *args, **kwargs: install_calls.append(True) or installed)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("launch needs another click"))
    parent = QWidget()
    dialog = updater.open_update_dialog(parent, "0.1.0")
    dialog.check()
    wait_for(qapp, lambda: not dialog._busy)
    assert install_calls == []
    assert not dialog.download_button.isHidden()
    assert "separate copy" in dialog.status_label.text()
    dialog.download_button.click()
    wait_for(qapp, lambda: dialog.installed is not None)
    assert install_calls == [True]
    assert not dialog.launch_button.isHidden()
    assert "previous copy and shortcuts are unchanged" in dialog.status_label.text()
    dialog.reject()
    qapp.processEvents()


def test_launch_failure_keeps_current_app_open(qapp, tmp_path, monkeypatch):
    executable = tmp_path / "Luma.exe"
    executable.write_bytes(b"MZexe")
    parent = QWidget()
    dialog = updater.open_update_dialog(parent, "0.1.0")
    dialog.installed = updater.InstalledUpdate("0.1.1", tmp_path, executable)
    monkeypatch.setattr(updater, "sys", SimpleNamespace(platform="win32"))
    def fail(*args, **kwargs):
        raise OSError("cannot start")
    monkeypatch.setattr(updater.subprocess, "Popen", fail)
    dialog.launch()
    assert dialog.isVisible()
    assert "current Luma is still running" in dialog.status_label.text()
    dialog.reject()
    qapp.processEvents()


def test_launch_is_explicit_shell_free_and_resets_frozen_environment(qapp, tmp_path, monkeypatch):
    executable = tmp_path / "Luma.exe"
    executable.write_bytes(b"MZexe")
    parent = QWidget()
    dialog = updater.open_update_dialog(parent, "0.1.0")
    dialog.installed = updater.InstalledUpdate("0.1.1", tmp_path, executable)
    monkeypatch.setattr(updater, "sys", SimpleNamespace(platform="win32"))
    launches, quits = [], []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *args, **kwargs: launches.append((args, kwargs)))
    monkeypatch.setattr(updater, "QApplication", SimpleNamespace(instance=lambda: SimpleNamespace(quit=lambda: quits.append(True))))
    dialog.launch()
    assert len(launches) == 1 and quits == [True]
    args, kwargs = launches[0]
    assert args == ([str(executable)],)
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["shell"] is False
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    qapp.processEvents()


def test_failed_extraction_cleans_staging_and_preserves_old_copy(tmp_path):
    data, package, manifest = release_data(zip_bytes([("Luma.exe", b"MZexe"), ("../bad", b"bad")]))
    release = updater.parse_release(data)
    opener = Opener({release.archive.url: package, release.checksums.url: manifest})
    (tmp_path / "existing").mkdir()
    (tmp_path / "existing" / "Luma.exe").write_bytes(b"old")
    with pytest.raises(updater.UpdateError, match="unsafe"):
        updater.install_release(release, tmp_path, opener=opener)
    assert list(tmp_path.iterdir()) == [tmp_path / "existing"]
    assert (tmp_path / "existing" / "Luma.exe").read_bytes() == b"old"


def test_repeated_install_uses_unique_directory(tmp_path):
    data, package, manifest = release_data()
    release = updater.parse_release(data)
    opener = Opener({release.archive.url: package, release.checksums.url: manifest})
    first = updater.install_release(release, tmp_path, opener=opener)
    second = updater.install_release(release, tmp_path, opener=opener)
    assert first.directory != second.directory
    assert first.executable.is_file() and second.executable.is_file()


def test_existing_nonempty_extraction_destination_is_never_overwritten(tmp_path):
    package = tmp_path / "update.zip"
    package.write_bytes(zip_bytes())
    destination = tmp_path / "extract"
    destination.mkdir()
    original = destination / "keep.txt"
    original.write_bytes(b"untouched")
    with pytest.raises(updater.UpdateError, match="not empty"):
        updater.safe_extract_zip(package, destination)
    assert original.read_bytes() == b"untouched"


def test_malformed_asset_name_is_rejected():
    data, _, _ = release_data()
    data["assets"][0]["name"] = ["invalid"]
    with pytest.raises(updater.UpdateError):
        updater.parse_release(data)
