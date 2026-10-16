"""Production-helper update integration on a disposable Windows CI runner.

Install the hash-pinned public 0.1.3 Setup, keep that GUI/DLL loaded, apply the
candidate 0.1.4 Setup using the source updater helper, then require the candidate's
actual frozen GUI to acknowledge its embedded version/path/PID after painting.
This is NOT a test of the old frozen application's historical updater UI.
No release is published and no remote state is changed.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.smoke_installer import APP_COMMAND, ARP, JOURNAL, SETTINGS, registered_uninstaller

BASELINE_VERSION = "0.1.3"
BASELINE_NAME = "Luma-0.1.3-windows-x64-setup.exe"
BASELINE_SHA256 = "c6622670955db4e25c02ceaeb42d432e2b8add73bba1777bbba09188d0ea5f4f"
BASELINE_SIZE = 35761843
BASELINE_URL = "https://github.com/Biscuits-Lab/Luma/releases/download/v0.1.3/" + BASELINE_NAME
CANDIDATE_VERSION = "0.1.4"
SETTING_NAME = "UpdateManagedSmokePreserve"
SETTING_VALUE = "keep the update smoke's original setting"
IMAGE_BYTES = b"user-owned image sentinel: never replace or delete during update"


def require_disposable_runner():
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise RuntimeError("Refusing update integration outside an ephemeral GitHub-hosted Windows runner.")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(128 * 1024):
            digest.update(block)
    return digest.hexdigest()


def check_baseline(path: Path) -> Path:
    path = path.resolve(strict=True)
    if (path.name != BASELINE_NAME or path.stat().st_size != BASELINE_SIZE
            or sha256(path) != BASELINE_SHA256):
        raise ValueError("The baseline must be the exact hash-pinned public 0.1.3 Setup.")
    return path


def check_candidate(path: Path, manifest: Path) -> tuple[Path, str]:
    from luma.updater import MAX_MANIFEST_BYTES, checksum_for, verify_checksum
    path = path.resolve(strict=True)
    manifest = manifest.resolve(strict=True)
    if path.name != f"Luma-{CANDIDATE_VERSION}-windows-x64-setup.exe":
        raise ValueError("The candidate must have the exact 0.1.4 Setup filename.")
    if manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("The build checksum manifest exceeds the safety limit.")
    expected = checksum_for(manifest.read_bytes(), path.name)
    verify_checksum(path, expected)
    return path, expected


def download_baseline(directory: Path) -> Path:
    from luma.updater import _transfer, validate_asset_url
    path = directory / BASELINE_NAME
    validate_asset_url(BASELINE_URL, "v0.1.3", BASELINE_NAME)
    with path.open("xb") as output:
        _transfer(BASELINE_URL, BASELINE_SIZE, output, expected_size=BASELINE_SIZE, allow_cdn=True)
    return check_baseline(path)


def visible_windows(pid: int) -> list[int]:
    from ctypes import wintypes
    api = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    handles = []
    @callback_type
    def callback(handle, unused):
        owner = wintypes.DWORD()
        api.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid and api.IsWindowVisible(handle):
            handles.append(handle)
        return True
    if not api.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return handles


def wait_for_window(process, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Luma exited before showing its native window: {process.returncode}")
        windows = visible_windows(process.pid)
        if windows:
            return windows
        time.sleep(0.1)
    raise TimeoutError("The baseline Luma did not show a native window before the deadline.")


def close_owned_gui(process):
    """Only this exact Popen handle is eligible; no process-name-wide termination."""
    if process.poll() is not None:
        return
    from ctypes import wintypes
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    for handle in visible_windows(process.pid):
        api.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE only to our captured PID.
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=10)


def wait_for_binary_release(root: Path, timeout=10) -> None:
    """Bounded post-exit guard for mapped DLLs/temporary AV handles, no writes."""
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    invalid = ctypes.c_void_p(-1).value
    binaries = [path for path in (root / "versions").rglob("*")
                if path.suffix.casefold() in (".exe", ".dll", ".pyd") and path.is_file()]
    if any(path.is_symlink() or path.is_junction() for path in binaries):
        raise RuntimeError("Refusing to inspect linked binaries during owned cleanup")
    deadline = time.monotonic() + timeout
    while True:
        busy = []
        for path in binaries:
            # OPEN_EXISTING and no write calls: this checks availability only.
            handle = api.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x80, None)
            if handle == invalid:
                busy.append(str(path))
            else:
                api.CloseHandle(handle)
        if not busy:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Owned binaries remain busy after GUI exit and ten-second guard: " + "; ".join(busy[:10]))
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True, help="Newly built 0.1.4 Setup")
    parser.add_argument("--checksums", type=Path, required=True, help="Build-generated SHA256SUMS")
    parser.add_argument("--baseline-installer", type=Path, help="Optional already downloaded, exact pinned 0.1.3 Setup")
    parser.add_argument("--output", type=Path, default=ROOT / "build/update-managed-smoke")
    args = parser.parse_args(argv)
    require_disposable_runner()  # Before any filesystem/registry/process mutation.
    import winreg
    from luma import shell_integration as shell
    from luma.installer import installed_executables
    from luma.update_installation import (
        _get_dll_directory, _windows_dll_api, apply_verified_setup, detect_installation,
    )
    from luma.update_probe import LaunchError, launch_verified_update

    logs = args.output.resolve()
    logs.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "test_kind": "candidate-source production helpers plus actual frozen GUI",
              "not_tested_here": "the legacy frozen in-app updater UI", "checks": []}
    work = None
    install_root = None
    native = None
    captured = []  # (purpose, exact Popen handle), never system-wide enumeration.
    streams = []
    seeded_setting = False
    baseline_defaults = None

    def read_value(key, name="", root=winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as opened:
                return winreg.QueryValueEx(opened, name)
        except FileNotFoundError:
            return None

    def key_exists(key, root=winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
                return True
        except FileNotFoundError:
            return False

    def defaults():
        result = {}
        for extension in shell.EXTENSIONS:
            result[extension] = read_value("Software\\Classes\\" + extension)
            key = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{extension}\UserChoice"
            for name in ("ProgId", "Hash"):
                result[extension + ":" + name] = read_value(key, name)
        return result

    def spawn(purpose):
        def launch(command, **kwargs):
            output = (logs / f"{purpose}-process.log").open("wb")
            streams.append(output)
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, **kwargs)
            captured.append((purpose, process))
            report.setdefault("processes", []).append({"purpose": purpose, "pid": process.pid,
                                                       "executable": str(command[0])})
            return process
        return launch

    def run(command, purpose, timeout=240):
        process = spawn(purpose)([str(value) for value in command], cwd=str(ROOT), shell=False, close_fds=True)
        code = process.wait(timeout=timeout)
        if code != 0:
            raise RuntimeError(f"{purpose} failed with native exit code {code}; inspect its process/Setup log")
        return process

    shortcuts = [Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Luma.lnk",
                 Path(os.environ["USERPROFILE"]) / "Desktop/Luma.lnk"]

    def shortcut_target(path):
        if not path.is_file():
            raise AssertionError(f"Expected shortcut is missing: {path}")
        command = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('" + str(path).replace("'", "''") + "'); $s.TargetPath"
        result = subprocess.run(["pwsh", "-NoProfile", "-Command", command], capture_output=True,
                                text=True, errors="replace", timeout=30, check=True)
        return Path(result.stdout.strip()).resolve(strict=True)

    def snapshot(executable, version):
        document, _ = shell._read_manifest(shell.WinRegistry())
        if document is None or document["state"] != "active" or not shell._owned_by_executable(document["data"], str(executable)):
            raise AssertionError("Shell registration does not belong to the exact expected managed payload")
        arp_version = read_value(ARP, "DisplayVersion")
        assert arp_version == (version, winreg.REG_SZ), "ARP version is not the actual installed target"
        assert read_value(ARP, "DisplayName")[0] == "Luma"
        assert read_value(ARP, "DisplayName", winreg.HKEY_LOCAL_MACHINE) is None
        command = read_value(APP_COMMAND)
        assert command is not None and command[0] == f'"{executable}" "%1"'
        targets = {str(path): str(shortcut_target(path)) for path in shortcuts}
        assert all(Path(target) == executable for target in targets.values()), "Shortcut still targets the older copy"
        assert defaults() == baseline_defaults, "Update changed extension defaults or UserChoice"
        return {"arp_version": arp_version[0], "owner_executable": str(executable),
                "application_open_command": command[0], "shortcuts": targets,
                "journal_sha256": hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest(),
                "windows_defaults_and_userchoice": defaults()}

    try:
        candidate, candidate_digest = check_candidate(args.installer, args.checksums)
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            assert not key_exists(ARP, root), "Refusing to overwrite an existing Luma installation"
        assert read_value(JOURNAL, "Manifest") is None, "Refusing to overwrite existing shell ownership"
        assert all(not path.exists() for path in shortcuts), "Refusing to overwrite an existing Luma shortcut"
        assert read_value(SETTINGS, SETTING_NAME) is None, "Refusing to replace an existing sentinel setting"
        baseline_defaults = defaults()
        report["initial_defaults"] = baseline_defaults
        work = Path(tempfile.mkdtemp(prefix="luma-update-managed-", dir=os.environ["RUNNER_TEMP"]))
        install_root = work / "Custom Luma Install"
        report["test_directory"] = str(work)
        baseline = check_baseline(args.baseline_installer) if args.baseline_installer else download_baseline(work)
        report["baseline_setup"] = {"path": str(baseline), "version": BASELINE_VERSION,
                                    "sha256": BASELINE_SHA256, "bytes": BASELINE_SIZE, "official_url": BASELINE_URL}
        report["candidate_setup"] = {"path": str(candidate), "version": CANDIDATE_VERSION,
                                     "sha256": candidate_digest, "bytes": candidate.stat().st_size}
        run([baseline, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/TASKS=desktopicon",
             f"/DIR={install_root}", f"/LOG={logs / 'baseline-setup.log'}"], "baseline-setup")
        old_candidates = list(install_root.glob("versions/*/Luma.exe"))
        assert len(old_candidates) == 1, "Baseline Setup did not produce exactly one managed payload"
        old_executable = old_candidates[0].resolve(strict=True)
        context = detect_installation(old_executable, frozen=True, platform="win32")
        assert context.mode == "managed" and context.version == BASELINE_VERSION and context.root == install_root.resolve()
        report["before"] = snapshot(old_executable, BASELINE_VERSION)
        report["checks"].append("hash-pinned public 0.1.3 installed into custom root with actual shortcuts/ARP/shell ownership")

        user_image = install_root / "preserve-my-photo.exr"
        user_image.write_bytes(IMAGE_BYTES)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, SETTINGS, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, SETTING_NAME, 0, winreg.REG_SZ, SETTING_VALUE)
        seeded_setting = True
        old_inventory = {str(path.relative_to(old_executable.parent)): sha256(path)
                         for path in old_executable.parent.rglob("*") if path.is_file()}
        old_gui = spawn("baseline-gui")([str(old_executable)], cwd=str(old_executable.parent), shell=False, close_fds=True)
        report["baseline_window_handles"] = [int(handle) for handle in wait_for_window(old_gui)]
        native = ctypes.WinDLL(str(old_executable.parent / "LumaThumbnail.dll"))

        # A source Python runner normally lacks PyInstaller's process-global
        # DLL setting. Exercise that exact native boundary explicitly so the CI
        # test cannot accidentally pass only because it used a source parent.
        dll_api = _windows_dll_api()
        original_dll_directory = _get_dll_directory(dll_api)
        bundled_dll_directory = str(old_executable.parent / "_internal")
        assert Path(bundled_dll_directory).is_dir()
        assert dll_api.SetDllDirectoryW(bundled_dll_directory), "Could not stage the real native frozen-parent DLL search path"
        setup_spawn = spawn("candidate-setup")
        def launch_external_setup(command, **kwargs):
            observed = _get_dll_directory(dll_api)
            report["setup_dll_search_path"] = {"staged_frozen_parent": bundled_dll_directory,
                                               "at_CreateProcess": observed}
            assert observed == "", "Setup would inherit the old frozen runtime DLL search path"
            return setup_spawn(command, **kwargs)
        try:
            updated = apply_verified_setup(candidate, candidate_digest, CANDIDATE_VERSION, context,
                popen=launch_external_setup, timeout=240,
                status=lambda text: report.setdefault("setup_status", []).append(text))
            restored = _get_dll_directory(dll_api)
            report["setup_dll_search_path"]["restored_after_spawn"] = restored
            assert restored == bundled_dll_directory, "Production helper did not restore the exact original DLL search directory"
        finally:
            assert dll_api.SetDllDirectoryW(original_dll_directory or None), "Could not restore the test runner's original DLL search directory"
        report["checks"].append("real Windows DLL search path was cleared only for external Setup creation and exactly restored afterward")
        assert old_gui.poll() is None, "Setup unexpectedly stopped the original running Luma"
        assert updated.previous_executable == old_executable and updated.root == context.root
        assert updated.executable in installed_executables(old_executable)
        report["after"] = snapshot(updated.executable, CANDIDATE_VERSION)
        assert {str(path.relative_to(old_executable.parent)): sha256(path)
                for path in old_executable.parent.rglob("*") if path.is_file()} == old_inventory, "Setup altered the loaded older payload"
        assert user_image.read_bytes() == IMAGE_BYTES
        assert read_value(SETTINGS, SETTING_NAME) == (SETTING_VALUE, winreg.REG_SZ)
        report["checks"].append("production apply helper completed Setup with old GUI/DLL live; shortcuts/ARP/owner advanced to 0.1.4 in the original root; old payload/images/settings/defaults retained")

        acknowledgment = launch_verified_update(updated.executable, CANDIDATE_VERSION,
                                                  popen=spawn("candidate-gui"), timeout=30)
        assert old_gui.poll() is None, "Old Luma disappeared before the new GUI was verified"
        candidate_gui = next(process for purpose, process in captured if purpose == "candidate-gui")
        assert acknowledgment.pid == candidate_gui.pid and visible_windows(candidate_gui.pid)
        assert acknowledgment.os_executable == updated.executable, "Windows process image did not independently match the candidate"
        report["startup_acknowledgment"] = {"version": acknowledgment.version,
                                           "executable": str(acknowledgment.executable), "pid": acknowledgment.pid,
                                           "windows_process_image": str(acknowledgment.os_executable),
                                           "native_window_handles": [int(handle) for handle in visible_windows(candidate_gui.pid)]}
        assert snapshot(updated.executable, CANDIDATE_VERSION)["owner_executable"] == str(updated.executable)
        report["checks"].append("actual 0.1.4 frozen GUI painted and acknowledged its embedded version, exact managed executable and live PID; original GUI remained open")

        legacy_portable = work / "legacy-portable"
        shutil.copytree(old_executable.parent, legacy_portable, ignore=shutil.ignore_patterns("luma-install.json"))
        try:
            launch_verified_update(legacy_portable / "Luma.exe", CANDIDATE_VERSION,
                                   popen=spawn("legacy-negative-probe"), timeout=8)
        except LaunchError as error:
            report["legacy_probe_rejection"] = str(error)
        else:
            raise AssertionError("An actual legacy 0.1.3 portable incorrectly passed the 0.1.4 startup probe")
        assert old_gui.poll() is None and candidate_gui.poll() is None
        assert defaults() == baseline_defaults
        report["checks"].append("actual 0.1.3 portable without the protocol cannot authorize a successful 0.1.4 relaunch")
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["traceback"] = traceback.format_exc()
    finally:
        # Never kill a Setup transaction to make cleanup appear successful.
        live_setups = []
        for purpose, process in reversed(captured):
            try:
                if "setup" in purpose and process.poll() is None:
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        live_setups.append(purpose)
                        raise RuntimeError(f"{purpose} is still running; leave its files/state for disposable-runner teardown")
                elif "gui" in purpose or "probe" in purpose:
                    close_owned_gui(process)
                report.setdefault("process_exit_codes", {})[purpose] = process.poll()
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(f"{purpose}: {error}")
        if native is not None:
            import _ctypes
            _ctypes.FreeLibrary(native._handle)
            native = None
        if install_root is not None and not live_setups:
            try:
                value = read_value(ARP, "UninstallString")
                if value is not None:
                    assert value[1] == winreg.REG_SZ
                    uninstaller = registered_uninstaller(value[0], install_root)
                    wait_for_binary_release(install_root, timeout=10)
                    result = subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                             f"/LOG={logs / 'cleanup-uninstall.log'}"], timeout=120,
                                            capture_output=True, text=True, errors="replace")
                    (logs / "cleanup-uninstall-process.log").write_text(result.stdout + result.stderr, encoding="utf-8")
                    assert result.returncode == 0, f"Owned uninstall failed with exit {result.returncode}"
                    assert read_value(ARP, "DisplayName") is None
                    assert read_value(JOURNAL, "Manifest") is None
                    assert all(not path.exists() for path in shortcuts)
                    assert defaults() == baseline_defaults
                    if seeded_setting:
                        assert (install_root / "preserve-my-photo.exr").read_bytes() == IMAGE_BYTES
                        assert read_value(SETTINGS, SETTING_NAME) == (SETTING_VALUE, winreg.REG_SZ)
                    report["checks"].append("captured GUIs fully exited before owned uninstall; images/settings/defaults survived cleanup")
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(f"owned uninstall: {error}")
        if seeded_setting and read_value(SETTINGS, SETTING_NAME) == (SETTING_VALUE, winreg.REG_SZ):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                    winreg.DeleteValue(key, SETTING_NAME)
            except OSError as error:
                report.setdefault("cleanup_errors", []).append(f"sentinel setting: {error}")
        for stream in streams:
            stream.close()
        if report.get("cleanup_errors"):
            report["passed"] = False
        (logs / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
