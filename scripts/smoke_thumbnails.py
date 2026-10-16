"""Run the guarded native-thumbnail smoke on a disposable Windows CI runner."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--harness", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    if os.name != "nt":
        parser.error("This smoke requires a disposable Windows x64 CI runner")
    if not any(
        os.environ.get(key, "").lower() in {"true", "1"}
        for key in ("CI", "GITHUB_ACTIONS")
    ):
        parser.error("CI=true or GITHUB_ACTIONS=true is required")
    if not 5 <= args.timeout <= 600:
        parser.error("--timeout must be between 5 and 600 seconds")

    powershell = shutil.which("pwsh")
    if not powershell:
        parser.error("PowerShell 7.2 or newer (pwsh) is required")

    command = [
        powershell, "-NoProfile", "-NonInteractive",
        "-File", str(ROOT / "scripts" / "smoke_thumbnails.ps1"),
        "-RepoRoot", str(ROOT),
        "-Python", sys.executable,
        "-TimeoutSeconds", str(args.timeout),
    ]
    for option, value in (
        ("-Bundle", args.bundle),
        ("-Harness", args.harness),
        ("-Artifacts", args.artifacts),
    ):
        if value is not None:
            command.extend((option, str(value.resolve())))

    # The PowerShell script bounds each child and owns registry cleanup.
    # Do not impose a shorter wrapper timeout that could bypass its finally.
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
