"""Compile the real Inno sources with output disabled and a disposable payload.

This standard-library-only check runs before the application build. It does not
build, execute, install, or publish a setup package. Real bundle validation and
installer UI/lifecycle checks remain mandatory later in the Windows workflow.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def compiler_path(explicit: str | None = None) -> Path:
    path = Path(explicit or shutil.which("ISCC.exe") or
                str(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) /
                    "Inno Setup 6/ISCC.exe"))
    if not path.is_file():
        raise FileNotFoundError("Inno Setup 6.7+ ISCC.exe is required for the compile-only preflight")
    return path.resolve(strict=True)


def compile_command(compiler: Path, root: Path, work: Path, version: str, scaled: bool) -> list[str]:
    # /O- is Inno's documented no-output option. Its output-directory cleanup is
    # confined to this newly allocated temporary directory, never artifacts/.
    command = [str(compiler), "/O-", f"/DAppVersion={version}",
               f"/DPayloadId={version}-{'0' * 24}", f"/DBundleDir={work / 'payload'}",
               f"/DMetadataFile={work / 'luma-install.json'}", f"/DArtifactDir={work / 'output'}",
               f"/DIntegrityFile={work / 'payload-hashes.iss'}",
               f"/DLauncherFile={work / 'launcher.exe'}",
               f"/DLauncherOwnerFile={work / 'launcher-owner.txt'}",
               f"/DAppIcon={root / 'luma/assets/icon.ico'}"]
    if scaled:
        command.append("/DLumaUiFontScaleTest=1")
    return command + [str(root / "packaging/windows/Luma.iss")]


def preflight(root: Path, compiler: Path) -> dict:
    output = root / "build/installer-preflight"
    output.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "mode": "Inno /O-; no setup output or execution",
              "compiler": str(compiler), "cases": []}
    try:
        version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        with tempfile.TemporaryDirectory(prefix="compile-only-", dir=output) as temporary:
            work = Path(temporary)
            (work / "payload").mkdir()
            # Deliberately not a PE or an executable application. Never launched.
            (work / "payload/Luma.exe").write_text("compile-only placeholder; not executable\n", encoding="utf-8")
            (work / "launcher.exe").write_text("compile-only launcher placeholder; not executable\n", encoding="utf-8")
            (work / "launcher-owner.txt").write_text("LUMA-LAUNCHER-1\n" + "0" * 64 + "\n", encoding="ascii")
            (work / "output").mkdir()
            (work / "luma-install.json").write_text("{}\n", encoding="utf-8")
            (work / "payload-hashes.iss").write_text(
                "procedure AddPayloadHashes(Manifest: TStrings);\nbegin\nend;\n", encoding="utf-8")
            for variant, scaled in (("normal", False), ("font-stress", True)):
                command = compile_command(compiler, root, work, version, scaled)
                result = subprocess.run(command, cwd=root, capture_output=True,
                                        text=True, errors="replace", timeout=90)
                (output / f"{variant}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
                row = {"variant": variant, "returncode": result.returncode,
                       "passed": result.returncode == 0, "log": f"{variant}.log"}
                report["cases"].append(row)
                if result.returncode:
                    print(result.stdout + result.stderr)
                    raise RuntimeError(f"{variant} Inno compile-only preflight failed ({result.returncode})")
                if any((work / "output").iterdir()):
                    raise RuntimeError("Compile-only preflight unexpectedly produced output files")
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", help="Existing trusted Inno Setup ISCC.exe path")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("The native Inno compile-only preflight requires Windows")
    report = preflight(ROOT, compiler_path(args.compiler))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
