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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARP = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{7F17F980-8965-4C81-8470-4880AD6B989F}_is1"
JOURNAL = r"Software\Biscuits Lab\Luma\ShellIntegration"
SETTINGS = r"Software\Biscuits Lab\Luma"


def registered_uninstaller(command: str, install_root: Path) -> Path:
    """Resolve only the installer-owned executable named by its ARP entry.

    Inno may allocate unins001.exe on an immediate reinstall while the previous
    unins000.exe is completing its asynchronous self-removal.
    """
    match = re.fullmatch(r'"([^"\r\n]+)"|([^"\r\n]+)', command)
    if match is None:
        raise ValueError("Unexpected uninstall command")
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

    logs = ROOT / "build" / "installer-smoke"
    logs.mkdir(parents=True, exist_ok=True)
    checks = []
    report = {"passed": False, "checks": checks, "installer": str(args.installer)}
    work = Path(tempfile.mkdtemp(prefix="luma-installer-", dir=os.environ["RUNNER_TEMP"]))
    install_root = work / "installed"
    installer = args.installer.resolve(strict=True)
    native = None

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
        for ext in ("png", "jpg", "tga", "dds", "hdr", "exr"):
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

    def uninstall(name, expected=0):
        value = read_value(winreg.HKEY_CURRENT_USER, ARP, "UninstallString")
        assert value is not None and value[1] == winreg.REG_SZ, "Missing installer ARP command"
        executable = registered_uninstaller(value[0], install_root)
        run([executable, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
             f"/LOG={logs / (name + '.log')}"], name, expected)

    def payloads():
        return sorted(p.parent for p in install_root.glob("versions/*/luma-install.json"))

    def explicit_opt_in(exe):
        # The isolated runner deliberately opts in using a real packaged EXE/DLL.
        # Only the frozen-interpreter guard is bypassed; PE/path checks still run.
        with patch.object(shell, "_runtime_guard", return_value=None):
            shell.install(exe)
        assert journal() is not None, "Explicit opt-in did not create the ownership journal"

    def unload():
        nonlocal native
        if native is not None:
            import _ctypes
            _ctypes.FreeLibrary(native._handle)
            native = None

    before_defaults = defaults()
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        assert read_value(root, ARP, "DisplayName") is None, "Refusing to overwrite an existing Luma installation"
    assert journal() is None, "Refusing to overwrite existing shell integration"
    settings_before = read_value(winreg.HKEY_CURRENT_USER, SETTINGS, "InstallerSmokePreserve")
    assert settings_before is None, "Refusing to overwrite an existing sentinel"
    shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Luma.lnk"
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop/Luma.lnk"
    assert not shortcut.exists() and not desktop.exists(), "Refusing to overwrite existing Luma shortcuts"
    try:
        install(installer, "install")
        assert read_value(winreg.HKEY_CURRENT_USER, ARP, "DisplayName")[0] == "Luma"
        assert read_value(winreg.HKEY_LOCAL_MACHINE, ARP, "DisplayName") is None
        assert journal() is None and defaults() == before_defaults
        assert shortcut.is_file() and not desktop.exists()
        first, = payloads()
        run([first / "Luma.exe", "--smoke-test"], "installed-exe", timeout=120)
        checks.append("current-user install, shortcuts, EXE launch, no automatic shell/default changes")

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
        assert Path(target) == second / "Luma.exe"
        checks.append("loaded native DLL survives reinstall and side-by-side upgrade; shortcut targets latest payload")

        explicit_opt_in(first / "Luma.exe")
        uninstall("uninstall-loaded-dll", expected=None)
        assert journal() is None and (second / "Luma.exe").is_file()
        unload()
        explicit_opt_in(first / "Luma.exe")
        uninstall("uninstall-all-installed-payloads")
        assert journal() is None
        assert read_value(winreg.HKEY_CURRENT_USER, ARP, "DisplayName") is None
        assert not shortcut.exists() and not desktop.exists()
        assert not list(install_root.glob("versions/*/Luma.exe"))
        assert not list(install_root.glob("versions/*/LumaThumbnail.dll"))
        assert user_image.read_bytes() == b"user-owned image sentinel"
        assert read_value(winreg.HKEY_CURRENT_USER, SETTINGS, "InstallerSmokePreserve")[0] == "keep my settings"
        assert defaults() == before_defaults
        checks.append("busy uninstall aborts safely; retry removes old owned registration and installed files, preserves images/settings/defaults")

        install(installer, "install-for-portable-owner")
        explicit_opt_in(ROOT / "dist" / "Luma" / "Luma.exe")
        portable_manifest = journal()
        uninstall("uninstall-preserves-portable-owner")
        assert journal() == portable_manifest and defaults() == before_defaults
        with patch.object(shell, "_runtime_guard", return_value=None):
            shell.uninstall_if_executable(ROOT / "dist" / "Luma" / "Luma.exe")
        assert journal() is None
        checks.append("uninstall preserves a different portable copy's registration")
        report["passed"] = True
        return 0
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        unload()
        # Only these explicit test-owned paths are candidates; never broad cleanup.
        for exe in [*(p / "Luma.exe" for p in payloads()), ROOT / "dist" / "Luma" / "Luma.exe"]:
            try:
                with patch.object(shell, "_runtime_guard", return_value=None):
                    shell.uninstall_if_executable(exe)
            except Exception as error:
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
