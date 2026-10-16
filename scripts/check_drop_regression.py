"""Prove reintroducing the original one-line drop bug fails the regression."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

BASE = "99841e74c180282f742847c0e471eed457999067"
canvas = Path("luma/canvas.py")
patched = canvas.read_bytes()
if patched.count(b"self.setAcceptDrops(False)") != 1:
    raise SystemExit("Expected exactly one canvas file-drop routing fix")
original = subprocess.check_output(["git", "show", f"{BASE}:luma/canvas.py"])
if b"self.setAcceptDrops(False)" in original:
    raise SystemExit("Baseline already contains the routing fix")
# Keep today's canvas API, zoom semantics and signals. Replacing the entire
# 0.1.0 class is no longer a valid negative control for the current window.
# QGraphicsView originally accepted scene drops by default; restore just that
# behavior and require the exact rejected-drag-move assertion, not any failure.
negative_control = patched.replace(b"self.setAcceptDrops(False)", b"self.setAcceptDrops(True)", 1)
env = os.environ.copy()
env["QT_QPA_PLATFORM"] = "offscreen"
env["PYTHONUTF8"] = "1"
env["PYTHONDONTWRITEBYTECODE"] = "1"
node = "tests/test_ui.py::test_drop_replaces_loaded_image_through_viewport[False]"
regression_ok = False
with tempfile.TemporaryDirectory(prefix="luma-drop-regression-") as directory:
    report = Path(directory) / "before.xml"
    try:
        canvas.write_bytes(negative_control)
        before = subprocess.run(
            [sys.executable, "-m", "pytest", node, "-q", "--tb=short", f"--junitxml={report}"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        print(before.stdout, flush=True)
        if report.exists():
            root = ET.parse(report).getroot()
            cases = list(root.iter("testcase"))
            failures = list(root.iter("failure"))
            errors = list(root.iter("error"))
            regression_ok = (
                before.returncode == 1 and len(cases) == 1
                and len(failures) == 1 and not errors
                and "assert moved.isAccepted()" in (failures[0].text or "")
            )
    finally:
        canvas.write_bytes(patched)
if not regression_ok:
    raise SystemExit("Reintroduced drop defect did not fail at the intended drag-move assertion")
print("Verified: restoring the original scene-drop behavior rejects the viewport drag move.", flush=True)
after = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_ui.py"], env=env)
if after.returncode:
    raise SystemExit(after.returncode)
print("Verified: one-line negative control fails and restored current UI regressions pass.", flush=True)
