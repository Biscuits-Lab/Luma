"""Observe one real, test-owned /SILENT lifecycle install; never simulate progress.

Called only by smoke_installer.py after its ephemeral-runner, registry and shortcut
isolation checks. The same lifecycle owns installation, assertions and uninstall.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import re
import time

from smoke_installer_ui import Native, InstallerJob, ULONG_PTR


class GUID(C.Structure):
    _fields_ = [("a", C.c_uint32), ("b", C.c_uint16), ("c", C.c_uint16), ("d", C.c_ubyte * 8)]


class VariantData(C.Union):
    _fields_ = [("integer", C.c_int32), ("pointer", C.c_void_p),
                ("record", C.c_void_p * 2), ("double", C.c_double)]


class VARIANT(C.Structure):
    _fields_ = [("vt", C.c_uint16), ("r1", C.c_uint16), ("r2", C.c_uint16),
                ("r3", C.c_uint16), ("data", VariantData)]


def accessible_progress(window: int) -> dict:
    """Read native MSAA role/value from this already ownership-checked HWND."""
    ole, automation, access = C.WinDLL("ole32"), C.WinDLL("oleaut32"), C.WinDLL("oleacc")
    ole.CoInitializeEx.argtypes, ole.CoInitializeEx.restype = [C.c_void_p, C.c_uint32], C.c_int32
    initialized = ole.CoInitializeEx(None, 2)
    if initialized < 0:
        raise OSError(f"CoInitializeEx failed: {initialized:#x}")
    pointer = C.c_void_p()
    try:
        iid = GUID(0x618736E0, 0x3C3D, 0x11CF, (C.c_ubyte * 8)(0x81, 0x0C, 0, 0xAA, 0, 0x38, 0x9B, 0x71))
        access.AccessibleObjectFromWindow.argtypes = [C.c_void_p, C.c_uint32, C.POINTER(GUID), C.POINTER(C.c_void_p)]
        access.AccessibleObjectFromWindow.restype = C.c_int32
        result = access.AccessibleObjectFromWindow(window, 0xFFFFFFFC, C.byref(iid), C.byref(pointer))
        if result < 0 or not pointer.value:
            raise OSError(f"Native progress accessibility unavailable: {result:#x}")
        table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
        child, role = VARIANT(), VARIANT()
        child.vt, child.data.integer = 3, 0  # VT_I4 / CHILDID_SELF
        get_role = C.WINFUNCTYPE(C.c_int32, C.c_void_p, VARIANT, C.POINTER(VARIANT))(table[13])
        result = get_role(pointer, child, C.byref(role))
        if result < 0 or role.vt != 3 or role.data.integer != 0x30:
            raise AssertionError("The rendered control is not an accessible native ProgressBar")
        def text_property(index: int) -> str:
            value = C.c_void_p()
            getter = C.WINFUNCTYPE(C.c_int32, C.c_void_p, VARIANT, C.POINTER(C.c_void_p))(table[index])
            result = getter(pointer, child, C.byref(value))
            try:
                if result < 0:
                    raise OSError(f"Native progress accessibility property failed: {result:#x}")
                return C.wstring_at(value) if value.value else ""
            finally:
                if value.value:
                    automation.SysFreeString.argtypes = [C.c_void_p]
                    automation.SysFreeString(value)
        value = text_property(11)
        if not value.strip():
            raise AssertionError("Native progress lost its accessible value")
        return {"role": role.data.integer, "role_name": "ProgressBar", "name": text_property(10), "value": value}
    finally:
        if pointer.value:
            table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
            C.WINFUNCTYPE(C.c_uint32, C.c_void_p)(table[2])(pointer)
        ole.CoUninitialize()


def progress_rows(path: Path) -> list[dict[str, int]]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return [dict((key, int(value)) for key, value in re.findall(r"([a-z_]+)=(-?\d+)", line))
            for line in raw.decode(encoding, errors="replace").splitlines()
            if "LUMA_PROGRESS window=" in line]


def progress_complete(row: dict, position: int, low: int, high: int) -> bool:
    # Inno maps its logical progress range to the native 0..65535 range.
    # Completion requires BOTH independent observations, not just a UI frame.
    if not (low == 0 and high == 65535 and low <= position <= high):
        raise AssertionError(f"Unexpected native progress range/value: position={position}, minimum={low}, maximum={high}")
    return row["maximum"] > 0 and row["current"] == row["maximum"] and position == high


def install_with_progress(command: list[str], log: Path, output: Path, timeout: int = 240) -> dict:
    """Run the same lifecycle operation visibly, with a bounded owned observer."""
    import os
    import sys
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise RuntimeError("Progress observation requires an ephemeral GitHub-hosted Windows runner")
    if "/SILENT" not in command or "/VERYSILENT" in command:
        raise ValueError("Native progress observation requires exactly the visible /SILENT mode")
    api, report = Native(), {"passed": False, "samples": [], "scope": "actual lifecycle install"}
    job = InstallerJob(api, command)
    try:
        deadline, captured, observed_complete = time.monotonic() + timeout, False, False
        while job.active():
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned installer exceeded its lifecycle deadline")
            rows = progress_rows(log)
            # Once the real engine and native gauge both finish, Setup replaces
            # or destroys the progress page. Do not query its stale HWND while
            # waiting for the still-required successful process exit.
            if rows and not observed_complete:
                row = rows[-1]
                window, gauge = row["window"], row["gauge"]
                if (api.owned(window, job.job) and api.owned(gauge, job.job)
                        and api.u.IsChild(window, gauge) and api.u.IsWindowVisible(gauge)):
                    def query(message: int, parameter=0) -> int:
                        result = ULONG_PTR()
                        api.check(api.u.SendMessageTimeoutW(gauge, message, parameter, 0, 3, 500, C.byref(result)),
                                  "Read actual native progress")
                        return result.value
                    position, low, high = query(0x0408), query(0x0407, 1), query(0x0407, 0)
                    observed_complete = progress_complete(row, position, low, high)
                    if observed_complete:
                        report["completed_observation"] = {"position": position, "minimum": low,
                            "maximum": high, "engine_telemetry": row}
                    if not report["samples"] or report["samples"][-1]["position"] != position:
                        report["samples"].append({"position": position, "minimum": low, "maximum": high,
                                                  "engine_telemetry": row})
                    if not captured and 0 < position < high:
                        report["accessibility"] = accessible_progress(gauge)
                        report["control"] = api.control(gauge)
                        screenshot = output.with_suffix(".png")
                        api.screenshot(window, screenshot)
                        report["screenshot"] = str(screenshot)
                        captured = True
            time.sleep(0.02)
        code = W.DWORD()
        api.check(api.k.GetExitCodeProcess(job.process, C.byref(code)), "Read owned setup exit")
        report["exit_code"] = code.value
        if code.value != 0:
            raise AssertionError(f"Visible lifecycle install returned {code.value}")
        report["engine_telemetry"] = progress_rows(log)
        if not captured:
            raise AssertionError("No real intermediate native progress frame was observed")
        positions = [row["current"] for row in report["engine_telemetry"]]
        if len(set(positions)) < 2 or positions != sorted(positions):
            raise AssertionError("Real installation telemetry did not advance monotonically")
        report["passed"] = True
        return report
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        job.close()
        report["forced_process_cleanup"] = job.forced_cleanup
        if job.forced_cleanup:
            report["passed"] = False
        report["engine_telemetry"] = progress_rows(log)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
