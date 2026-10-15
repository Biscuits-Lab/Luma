#!/usr/bin/env bash
# Development smoke bundle only. This does not produce or certify a Windows EXE.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This smoke script is for Linux. Use build_windows.ps1 on Windows." >&2
  exit 2
fi
"$PYTHON" scripts/collect_licenses.py --output build/licenses --allow-incomplete
"$PYTHON" -m PyInstaller --clean --noconfirm Luma.spec
"$PYTHON" scripts/verify_bundle.py dist/Luma
printf '%s\n' 'Linux development smoke build only. Not a Windows executable or redistribution-ready release.' > dist/Luma/DEVELOPMENT_ONLY.txt
printf '%s\n' 'Built dist/Luma/Luma. Start under QT_QPA_PLATFORM=offscreen only for automated smoke checks.'
