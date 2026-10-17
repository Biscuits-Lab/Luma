"""Real installer lifecycle tests, only on an ephemeral GitHub Windows runner.

This intentionally writes the runner's HKCU and Start menu. Never run it on a
developer/user desktop. It does not publish a release or upload an artifact.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARP = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{7F17F980-8965-4C81-8470-4880AD6B989F}_is1"
JOURNAL = r"Software\Biscuits Lab\Luma\ShellIntegration"
SETTINGS = r"Software\Biscuits Lab\Luma"
APP_COMMAND = r"Software\Classes\Applications\Luma.exe\shell\open\command"


def registered_uninstaller(command: str, install_root: Path) -> Path:
    """Resolve only the installer-owned executable named by its ARP entry.

    Inno may allocate unins001.exe on an immediate reinstall while the previous
    unins000.exe is completing its asynchronous self-removal. With
    UninstallLogging=yes, Inno appends exactly " /LOG" to the quoted path
    (issrc is-6_7_1, Projects/Src/Setup.Install.pas, lines 304-310).
    Ignore only that suffix; never execute arguments taken from the registry.
    """
    match = re.fullmatch(r'"([^"\r\n]+)"(?: /LOG)?|([^"\r\n]+)', command)
    if match is None:
        raise ValueError(f"Unexpected uninstall command: {command!r}")
    executable = Path(match.group(1) or match.group(2)).resolve(strict=True)
    if (executable.parent != install_root.resolve(strict=True)
            or not re.fullmatch(r"unins[0-9]{3}\.exe", executable.name, re.IGNORECASE)
            or not executable.is_file()):
        raise ValueError("Uninstaller is outside the test-owned installation")
    return executable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise SystemExit("Refusing installer smoke outside an ephemeral GitHub-hosted Windows runner.")
    import winreg
    from luma import shell_integration as shell
    from luma.managed_launch import read_active_payload

    logs = ROOT / "build" / "installer-smoke"
    logs.mkdir(parents=True, exist_ok=True)
    checks = []
    report = {"passed": False, "checks": checks, "installer": str(args.installer)}
    work = Path(tempfile.mkdtemp(prefix="luma-installer-", dir=os.environ["RUNNER_TEMP"]))
    install_root = work / "installed"
    root_exe = install_root / "Luma.exe"
    active_pointer = install_root / "active-payload.txt"
    launcher_owner = install_root / "launcher-owner.txt"
    installer = args.installer.resolve(strict=True)
    native = None
    manual_command = None

    def read_value(root, key, name=""):
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as opened:
                return winreg.QueryValueEx(opened, name)
        except FileNotFoundError:
            return None

    def journal():
        return read_value(winreg.HKEY_CURRENT_USER, JOURNAL, "Manifest")

    def defaults():
        result = {}
        for ext in (extension[1:] for extension in shell.EXTENSIONS):
            result[ext] = read_value(winreg.HKEY_CURRENT_USER, rf"Software\Classes\.{ext}")
            for value in ("ProgId", "Hash"):
                key = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.{ext}\UserChoice"
                result[ext + value] = read_value(winreg.HKEY_CURRENT_USER, key, value)
        return result

    def run(command, name, expected=0, timeout=240):
        result = subprocess.run([str(p) for p in command], cwd=ROOT, timeout=timeout,
                                capture_output=True, text=True, errors="replace")
        (logs / f"{name}-process.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if expected is None:
            if result.returncode == 0:
                raise AssertionError(f"{name} unexpectedly succeeded")
        elif result.returncode != expected:
            raise AssertionError(f"{name} returned {result.returncode}; see its log")

    def install(package, name, expected=0):
        run([package, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
             f"/DIR={install_root}", "/TASKS=", f"/LOG={logs / (name + '.log')}"], name, expected)
        raw = (logs / (name + ".log")).read_bytes()
        text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig", errors="replace")
        assert "Luma interactive registration repair result:" not in text, "Silent Setup invoked interactive owner repair"

    def assert_open_with_discovery(exe, name):
        # Fresh process avoids stale Shell caches after side-by-side upgrades.
        # The helper only enumerates; it never launches or promotes a handler.
        output = logs / f"{name}-report.json"
        run([sys.executable, ROOT / "scripts/probe_open_with.py",
             "--executable", exe, "--output", output], name, timeout=120)
        discovery = json.loads(output.read_text(encoding="utf-8"))
        assert discovery["passed"] and len(discovery["extensions"]) == len(shell.EXTENSIONS)
        assert defaults() == before_defaults, "Open With enumeration changed Windows defaults"
        report.setdefault("open_with_discovery", []).append(discovery)

    def uninstall(name, expected=0):
        value = read_value(winreg.HKEY_CURRENT_USER, ARP, "UninstallString")
        assert value is not None and value[1] == winreg.REG_SZ, "Missing installer ARP command"
        executable = registered_uninstaller(value[0], install_root)
        run([executable, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
             f"/LOG={logs / (name + '.log')}"], name, expected)

    def payloads():
        return sorted(p.parent for p in install_root.glob("versions/*/luma-install.json"))

    def set_test_owner(exe):
        # The isolated runner deliberately stages ownership using a real EXE/DLL.
        # Only the frozen-interpreter guard is bypassed; PE/path checks still run.
        with patch.object(shell, "_runtime_guard", return_value=None):
            shell.install(exe)
        assert journal() is not None, "Test-owner registration did not create the journal"

    def assert_owner(exe):
        document, _ = shell._read_manifest(shell.WinRegistry())
        assert document is not None and document["state"] == "active"
        assert shell._owned_by_executable(document["data"], str(exe.resolve(strict=True)))
        assert document["data"]["thumbnails_enabled"] is True
        assert defaults() == before_defaults, "Setup changed Windows defaults"
        hdr = r"Software\Classes\Luma.Image.hdr"
        assert read_value(winreg.HKEY_CURRENT_USER, hdr + r"\shell\open\command")[0] == f'"{root_exe.resolve(strict=True)}" "%1"'
        assert read_value(winreg.HKEY_CURRENT_USER, APP_COMMAND)[0] == f'"{root_exe.resolve(strict=True)}" "%1"'
        assert read_value(winreg.HKEY_CURRENT_USER, hdr + "\\shellex\\" + shell.THUMBNAIL_IID)[0] == shell.PROVIDERS["hdr"]
        assert read_value(winreg.HKEY_CURRENT_USER, shell.CAPABILITIES + r"\FileAssociations", ".hdr")[0] == "Luma.Image.hdr"
        app_provider = read_value(winreg.HKEY_CURRENT_USER,
            r"Software\Classes\Applications\Luma.exe\shellex" + "\\" + shell.THUMBNAIL_IID)
        assert app_provider is not None and app_provider[0] in shell.PROVIDERS.values(), "Open with application route has no Luma thumbnail provider"

    def unload():
        nonlocal native
        if native is not None:
            import _ctypes
            _ctypes.FreeLibrary(native._handle)
            native = None

    def assert_running_root_blocks_uninstall():
        from smoke_installer_ui import Native, InstallerJob, WM_CLOSE
        api = Native()
        job = InstallerJob(api, [str(root_exe.resolve(strict=True))])
        observed = {"passed": False, "root_pid": job.pid, "root": str(root_exe)}
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                children = [api.control(window) for window in api.windows(job.job)
                            if api.u.IsWindowVisible(window)]
                children = [row for row in children if row["pid"] != job.pid]
                if job.active() >= 2 and children:
                    observed["child_pid"] = children[0]["pid"]
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("The stable root did not retain a visible payload child")
            pointer = active_pointer.read_bytes()
            uninstall("uninstall-running-root", expected=None)
            assert active_pointer.read_bytes() == pointer, "Busy root uninstall changed the active payload"
            raw = (logs / "uninstall-running-root.log").read_bytes()
            text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig", errors="replace")
            assert "LUMA_BUSY open_failed;win32_error=" in text and str(root_exe) in text
            observed["passed"] = True
        finally:
            for window in api.windows(job.job):
                if api.u.IsWindowVisible(window):
                    api.u.PostMessageW(window, WM_CLOSE, 0, 0)
            deadline = time.monotonic() + 30
            while job.active() and time.monotonic() < deadline:
                time.sleep(0.1)
            job.close()
            observed["forced_cleanup"] = job.forced_cleanup
            report["running_root_uninstall"] = observed
            if job.forced_cleanup:
                raise AssertionError("The owned root/payload processes did not exit normally")

    before_defaults = defaults()
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        assert read_value(root, ARP, "DisplayName") is None, "Refusing to overwrite an existing Luma installation"
    assert journal() is None, "Refusing to overwrite existing shell integration"
    settings_before = read_value(winreg.HKEY_CURRENT_USER, SETTINGS, "InstallerSmokePreserve")
    assert settings_before is None, "Refusing to overwrite an existing sentinel"
    shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Luma.lnk"
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop/Luma.lnk"
    assert not shortcut.exists() and not desktop.exists(), "Refusing to overwrite existing Luma shortcuts"
    conflict_key = r"Software\Classes\Luma.Image.hdr\shellex" + "\\" + shell.THUMBNAIL_IID
    conflict_value = "{11111111-2222-3333-4444-555555555555}"
    assert read_value(winreg.HKEY_CURRENT_USER, conflict_key) is None, "Refusing to overwrite an existing provider"
    conflict_seeded = False
    try:
        install_root.mkdir()
        root_exe.write_bytes(b"test-owned unrecognized root executable")
        install(installer, "reject-unowned-root-entry", expected=None)
        assert root_exe.read_bytes() == b"test-owned unrecognized root executable"
        assert not payloads() and journal() is None and not launcher_owner.exists()
        root_exe.unlink()  # Only this exact disposable test sentinel.
        checks.append("unknown preexisting root executable is rejected without overwrite or payload extraction")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, conflict_key, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, conflict_value)
        conflict_seeded = True
        install(installer, "install-preserves-foreign-provider", expected=10)
        assert journal() is None and defaults() == before_defaults
        assert read_value(winreg.HKEY_CURRENT_USER, conflict_key)[0] == conflict_value
        assert len(payloads()) == 1, "Failed registration must retain installed files for safe retry"
        assert not active_pointer.exists(), "Failed fresh registration published an active root payload"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, conflict_key, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            winreg.DeleteValue(key, "")
        conflict_seeded = False
        checks.append("automatic registration conflict preserves foreign provider/defaults and reports setup exit 10")

        # Exercise real extraction progress within this already isolated lifecycle,
        # rather than manufacturing a progress bar on the configuration page.
        from smoke_installer_progress import install_with_progress
        report["native_progress"] = install_with_progress(
            [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/TASKS=",
             f"/DIR={install_root}", f"/LOG={logs / 'install.log'}"],
            logs / "install.log", logs / "native-progress.json")
        assert read_value(winreg.HKEY_CURRENT_USER, ARP, "DisplayName")[0] == "Luma"
        assert read_value(winreg.HKEY_LOCAL_MACHINE, ARP, "DisplayName") is None
        assert shortcut.is_file() and not desktop.exists()
        first, = payloads()
        assert root_exe.is_file() and launcher_owner.is_file()
        assert read_active_payload(install_root).executable == (first / "Luma.exe").resolve(strict=True)
        launcher_bytes = root_exe.read_bytes()
        assert_owner(first / "Luma.exe")
        assert_open_with_discovery(root_exe, "open-with-after-install")
        run([root_exe, "--smoke-test"], "installed-root-exe", timeout=120)
        checks.append("current-user install, shortcuts, EXE launch, automatic HDR/application-route registration, unchanged defaults")
        assert_running_root_blocks_uninstall()
        install(installer, "repair-after-busy-root-uninstall")
        assert_owner(first / "Luma.exe")
        checks.append("live root launcher retains its payload child; uninstall refuses the mapped root and preserves its active pointer")

        user_image = install_root / "my-photo.png"
        user_image.write_bytes(b"user-owned image sentinel")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SETTINGS) as key:
            winreg.SetValueEx(key, "InstallerSmokePreserve", 0, winreg.REG_SZ, "keep my settings")

        inventory = first / "bundle-inventory.txt"
        original = inventory.read_bytes()
        inventory.write_bytes(original + b"\nmodified\n")
        try:
            install(installer, "reject-modified-payload", expected=None)
        finally:
            inventory.write_bytes(original)
        checks.append("same-payload reinstall rejects changed existing files")

        old_dll_hash = hashlib.sha256((first / "LumaThumbnail.dll").read_bytes()).hexdigest()
        native = ctypes.WinDLL(str(first / "LumaThumbnail.dll"))
        install(installer, "reinstall-loaded-dll")
        assert payloads() == [first]
        assert_owner(first / "Luma.exe")
        # Simulate Windows' manual Browse registration for the old installed
        # executable, without a Luma journal. Only this test-owned command is
        # seeded; real extension defaults and UserChoice are never written.
        with patch.object(shell, "_runtime_guard", return_value=None):
            shell.uninstall_if_executable(first / "Luma.exe")
        assert journal() is None and defaults() == before_defaults
        assert read_value(winreg.HKEY_CURRENT_USER, APP_COMMAND) is None
        manual_command = f'"{(first / "Luma.exe").resolve(strict=True)}" "%1"'
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, APP_COMMAND, 0,
                               winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, manual_command)
        assert read_value(winreg.HKEY_CURRENT_USER, APP_COMMAND) == (manual_command, winreg.REG_SZ)
        # A repackaged build with the same app version gets its own immutable path.
        second_bundle = work / "upgrade-bundle"
        shutil.copytree(ROOT / "dist" / "Luma", second_bundle)
        (second_bundle / "installer-upgrade-probe.txt").write_text("CI side-by-side upgrade fixture\n")
        upgrade_artifacts = work / "upgrade-artifacts"
        run(["pwsh", "-NoProfile", "-File", ROOT / "scripts/build_installer.ps1",
             "-BundleDir", second_bundle, "-OutputDir", upgrade_artifacts], "build-upgrade", timeout=600)
        upgrade_package, = upgrade_artifacts.glob("*-setup.exe")
        install(upgrade_package, "upgrade-loaded-dll")
        assert len(payloads()) == 2 and first in payloads()
        second, = [p for p in payloads() if p != first]
        assert hashlib.sha256((first / "LumaThumbnail.dll").read_bytes()).hexdigest() == old_dll_hash
        command = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('" + str(shortcut).replace("'", "''") + "'); $s.TargetPath"
        target = subprocess.check_output(["pwsh", "-NoProfile", "-Command", command], text=True).strip()
        assert Path(target).resolve(strict=True) == root_exe.resolve(strict=True)
        assert root_exe.read_bytes() == launcher_bytes, "Upgrade replaced the compatible stable launcher"
        assert read_active_payload(install_root).executable == (second / "Luma.exe").resolve(strict=True)
        assert_owner(second / "Luma.exe")
        assert_open_with_discovery(root_exe, "open-with-after-upgrade")
        run([root_exe, "--smoke-test"], "upgraded-root-exe", timeout=120)
        checks.append("loaded native DLL survives reinstall and side-by-side upgrade; stable root shortcut and automatic registration resolve the latest immutable payload")
        checks.append("automatic upgrade adopts only the exact unjournaled manual-Browse command for its validated older sibling; latest Applications command and Windows defaults verified")
        checks.append("native Open With discovery finds the stable root Luma executable and display name for every supported extension after install and upgrade; recommendation is diagnostic only")

        pointer_before_busy = active_pointer.read_bytes()
        uninstall("uninstall-loaded-dll", expected=None)
        assert active_pointer.read_bytes() == pointer_before_busy, "Busy uninstall broke the stable launch pointer"
        assert journal() is None and (second / "Luma.exe").is_file()
        assert read_value(winreg.HKEY_CURRENT_USER, APP_COMMAND) is None, "Uninstall restored a stale manual-Browse command"
        unload()
        set_test_owner(first / "Luma.exe")
        uninstall("uninstall-all-installed-payloads")
        assert journal() is None
        assert read_value(winreg.HKEY_CURRENT_USER, ARP, "DisplayName") is None
        assert not shortcut.exists() and not desktop.exists()
        assert not root_exe.exists() and not active_pointer.exists() and not launcher_owner.exists()
        for state_name in ("managed-uninstall.ready", "managed-uninstall.pending.json", "active-payload.pending.json"):
            assert not (install_root / state_name).exists(), "Successful uninstall left managed transaction state"
        assert not list(install_root.glob("versions/*/Luma.exe"))
        assert not list(install_root.glob("versions/*/LumaThumbnail.dll"))
        assert read_value(winreg.HKEY_CURRENT_USER, APP_COMMAND) is None, "Managed uninstall left a stale application command"
        assert user_image.read_bytes() == b"user-owned image sentinel"
        assert read_value(winreg.HKEY_CURRENT_USER, SETTINGS, "InstallerSmokePreserve")[0] == "keep my settings"
        assert defaults() == before_defaults
        checks.append("busy uninstall aborts safely; retry removes old owned registration and installed files, preserves images/settings/defaults")

        # Reproduce an older uninstall preserving a Windows-created, unowned
        # Browse command after the referenced payload directory was removed.
        removed_payload = install_root / "versions" / ("0.0.1-" + "c" * 24)
        for empty_directory in (False, True):
            assert not removed_payload.exists()
            if empty_directory:
                removed_payload.mkdir(parents=True)
            manual_command = f'"{removed_payload / "Luma.exe"}" "%1"'
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, APP_COMMAND, 0,
                                   winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, manual_command)
            suffix = "empty" if empty_directory else "missing"
            install(installer, "reinstall-after-removed-browse-owner-" + suffix)
            reinstalled, = payloads()
            assert_owner(reinstalled / "Luma.exe")
            assert read_active_payload(install_root).executable == (reinstalled / "Luma.exe").resolve(strict=True)
            uninstall("uninstall-repaired-browse-owner-" + suffix)
            assert journal() is None and defaults() == before_defaults
            assert read_value(winreg.HKEY_CURRENT_USER, APP_COMMAND) is None
            assert removed_payload.exists() == empty_directory
        checks.append("reinstall repairs an unowned Open With command for a removed same-root payload with missing or empty old directory; uninstall removes the adopted command and preserves Windows defaults")

        install(installer, "install-for-portable-owner")
        set_test_owner(ROOT / "dist" / "Luma" / "Luma.exe")
        portable_manifest = journal()
        pointer_before_conflict = active_pointer.read_bytes()
        install(installer, "reinstall-preserves-portable-owner", expected=10)
        assert active_pointer.read_bytes() == pointer_before_conflict, "Registration conflict changed active payload"
        assert journal() == portable_manifest and defaults() == before_defaults
        uninstall("uninstall-preserves-portable-owner")
        assert journal() == portable_manifest and defaults() == before_defaults
        checks.append("automatic reinstall and uninstall preserve a different portable copy's registration")
        # Preserve the existing foreign-owner assertions above, then test an
        # explicit user's choice separately using the real packaged GUI helper.
        install(installer, "install-for-confirmed-repair", expected=10)
        assert journal() == portable_manifest and defaults() == before_defaults
        managed, = payloads()
        from smoke_registration_confirmation import exercise_confirmation
        report["registration_confirmation"] = [exercise_confirmation(
            managed / "Luma.exe", ROOT / "dist/Luma/Luma.exe",
            logs / "registration-default-no.json", confirm=False)]
        assert journal() == portable_manifest and defaults() == before_defaults
        report["registration_confirmation"].append(exercise_confirmation(
            managed / "Luma.exe", ROOT / "dist/Luma/Luma.exe",
            logs / "registration-confirm-yes.json", confirm=True))
        assert_owner(managed / "Luma.exe")
        assert read_active_payload(install_root).executable == (managed / "Luma.exe").resolve(strict=True)
        uninstall("uninstall-confirmed-repair")
        assert journal() is None and defaults() == before_defaults
        with patch.object(shell, "_runtime_guard", return_value=None):
            shell.uninstall_if_executable(ROOT / "dist" / "Luma" / "Luma.exe")
        assert journal() is None
        checks.append("packaged confirmation shows exact owner paths; Return defaults to No without registry change; explicit Yes migrates owned entries; defaults unchanged and managed uninstall cleans up")
        report["passed"] = True
        return 0
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        unload()
        if conflict_seeded and read_value(winreg.HKEY_CURRENT_USER, conflict_key) == (conflict_value, winreg.REG_SZ):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, conflict_key, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                winreg.DeleteValue(key, "")
        # Only these explicit test-owned paths are candidates; never broad cleanup.
        for exe in [*(p / "Luma.exe" for p in payloads()), ROOT / "dist" / "Luma" / "Luma.exe"]:
            try:
                with patch.object(shell, "_runtime_guard", return_value=None):
                    shell.uninstall_if_executable(exe)
            except Exception as error:
                report.setdefault("cleanup_errors", []).append(str(error))
        # A failure before adoption may leave precisely our unjournaled seed.
        # Never remove a replacement value or another copy's command.
        if manual_command is not None and read_value(winreg.HKEY_CURRENT_USER, APP_COMMAND) == (manual_command, winreg.REG_SZ):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_COMMAND, 0,
                                    winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                    winreg.DeleteValue(key, "")
            except OSError as error:
                report.setdefault("cleanup_errors", []).append(str(error))
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "InstallerSmokePreserve")
        except FileNotFoundError:
            pass
        report["test_directory"] = str(work)
        (logs / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
