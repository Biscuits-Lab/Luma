"""Capture the installer configuration page without ever starting installation.

Only an ephemeral GitHub-hosted Windows runner is allowed. The installer is
created suspended and assigned to a private kill-on-close job before it runs;
all window inspection/actions are restricted to that job's processes. The only
UI actions open/cancel its directory chooser, then close its wizard and confirm
the localized cancellation. Installation is never started.

Dependency: Pillow (already pinned in requirements.txt). Optional font-scaled
compiler output is a layout stress test, not evidence of real high-DPI testing.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("zhCN", "zhTW", "en")
HANDLE = C.c_void_p
ULONG_PTR = C.c_size_t
LONG_PTR = C.c_ssize_t
DWORD = C.c_uint32
BOOL = C.c_int32
LONG = C.c_int32
WORD = C.c_uint16
WM_CLOSE, WM_GETTEXT, BM_CLICK, BM_GETCHECK = 0x0010, 0x000D, 0x00F5, 0x00F0


class STARTUPINFO(C.Structure):
    _fields_ = [("cb", DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR),
                ("lpTitle", W.LPWSTR), ("dwX", DWORD), ("dwY", DWORD),
                ("dwXSize", DWORD), ("dwYSize", DWORD), ("dwXCountChars", DWORD),
                ("dwYCountChars", DWORD), ("dwFillAttribute", DWORD),
                ("dwFlags", DWORD), ("wShowWindow", WORD), ("cbReserved2", WORD),
                ("lpReserved2", HANDLE), ("hStdInput", HANDLE),
                ("hStdOutput", HANDLE), ("hStdError", HANDLE)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE),
                ("dwProcessId", DWORD), ("dwThreadId", DWORD)]


class JOB_BASIC_LIMIT(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_int64),
                ("PerJobUserTimeLimit", C.c_int64), ("LimitFlags", DWORD),
                ("MinimumWorkingSetSize", ULONG_PTR), ("MaximumWorkingSetSize", ULONG_PTR),
                ("ActiveProcessLimit", DWORD), ("Affinity", ULONG_PTR),
                ("PriorityClass", DWORD), ("SchedulingClass", DWORD)]


class IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOB_EXTENDED_LIMIT(C.Structure):
    _fields_ = [("BasicLimitInformation", JOB_BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ULONG_PTR), ("JobMemoryLimit", ULONG_PTR),
                ("PeakProcessMemoryUsed", ULONG_PTR), ("PeakJobMemoryUsed", ULONG_PTR)]


class JOB_ACCOUNTING(C.Structure):
    _fields_ = [("TotalUserTime", C.c_int64), ("TotalKernelTime", C.c_int64),
                ("ThisPeriodTotalUserTime", C.c_int64), ("ThisPeriodTotalKernelTime", C.c_int64),
                ("TotalPageFaultCount", DWORD), ("TotalProcesses", DWORD),
                ("ActiveProcesses", DWORD), ("TotalTerminatedProcesses", DWORD)]


class RECT(C.Structure):
    _fields_ = [(name, LONG) for name in ("left", "top", "right", "bottom")]


class POINT(C.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class BITMAPINFOHEADER(C.Structure):
    _fields_ = [("biSize", DWORD), ("biWidth", LONG), ("biHeight", LONG),
                ("biPlanes", WORD), ("biBitCount", WORD), ("biCompression", DWORD),
                ("biSizeImage", DWORD), ("biXPelsPerMeter", LONG),
                ("biYPelsPerMeter", LONG), ("biClrUsed", DWORD), ("biClrImportant", DWORD)]


def caption(value: str) -> str:
    value = re.sub(r"\(&[A-Za-z]\)", "", value)
    return " ".join(value.replace("&", "").replace("%n", "\n").split())


def messages(language: str) -> dict[str, str]:
    folder = ROOT / "packaging/windows/languages"
    files = [folder / "Reference-Default.isl"]
    if language == "zhCN":
        files.append(folder / "ChineseSimplified.isl")
    elif language == "zhTW":
        files += [folder / "ChineseTraditional.isl", folder / "ChineseTraditionalOverrides.isl"]
    values = {}
    for path in files:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith(";"):
                key, value = line.split("=", 1)
                values[key.strip()] = value
    for line in (folder / "LumaMessages.iss").read_text(encoding="utf-8-sig").splitlines():
        if line.startswith(language + ".") and "=" in line:
            key, value = line.split("=", 1)
            values[key[len(language) + 1:]] = value
    return values


def parse_metrics(log: Path) -> list[dict[str, int]]:
    if not log.exists():
        return []
    data = log.read_bytes()
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    rows = []
    for line in data.decode(encoding, errors="replace").splitlines():
        if "LUMA_UI " in line:
            payload = line.split("LUMA_UI ", 1)[1]
            pairs = re.findall(r"([a-z_0-9]+)=(-?\d+)", payload)
            if pairs:
                rows.append({key: int(value) for key, value in pairs})
    return rows


class Native:
    def __init__(self):
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.u = C.WinDLL("user32", use_last_error=True)
        self.g = C.WinDLL("gdi32", use_last_error=True)
        self.callback = C.WINFUNCTYPE(BOOL, HANDLE, LONG_PTR)
        bindings = [
            (self.k, "CreateJobObjectW", HANDLE, [HANDLE, W.LPCWSTR]),
            (self.k, "SetInformationJobObject", BOOL, [HANDLE, C.c_int, HANDLE, DWORD]),
            (self.k, "QueryInformationJobObject", BOOL, [HANDLE, C.c_int, HANDLE, DWORD, HANDLE]),
            (self.k, "AssignProcessToJobObject", BOOL, [HANDLE, HANDLE]),
            (self.k, "IsProcessInJob", BOOL, [HANDLE, HANDLE, C.POINTER(BOOL)]),
            (self.k, "CreateProcessW", BOOL, [W.LPCWSTR, W.LPWSTR, HANDLE, HANDLE,
                 BOOL, DWORD, HANDLE, W.LPCWSTR, C.POINTER(STARTUPINFO), C.POINTER(PROCESS_INFORMATION)]),
            (self.k, "ResumeThread", DWORD, [HANDLE]),
            (self.k, "OpenProcess", HANDLE, [DWORD, BOOL, DWORD]),
            (self.k, "CloseHandle", BOOL, [HANDLE]),
            (self.k, "TerminateProcess", BOOL, [HANDLE, C.c_uint]),
            (self.k, "TerminateJobObject", BOOL, [HANDLE, C.c_uint]),
            (self.k, "GetExitCodeProcess", BOOL, [HANDLE, C.POINTER(DWORD)]),
            (self.u, "EnumWindows", BOOL, [self.callback, LONG_PTR]),
            (self.u, "EnumChildWindows", BOOL, [HANDLE, self.callback, LONG_PTR]),
            (self.u, "GetWindowThreadProcessId", DWORD, [HANDLE, C.POINTER(DWORD)]),
            (self.u, "GetWindowRect", BOOL, [HANDLE, C.POINTER(RECT)]),
            (self.u, "GetClientRect", BOOL, [HANDLE, C.POINTER(RECT)]),
            (self.u, "ClientToScreen", BOOL, [HANDLE, C.POINTER(POINT)]),
            (self.u, "GetClassNameW", C.c_int, [HANDLE, W.LPWSTR, C.c_int]),
            (self.u, "IsWindow", BOOL, [HANDLE]),
            (self.u, "IsWindowVisible", BOOL, [HANDLE]),
            (self.u, "IsWindowEnabled", BOOL, [HANDLE]),
            (self.u, "IsChild", BOOL, [HANDLE, HANDLE]),
            (self.u, "GetDlgCtrlID", C.c_int, [HANDLE]),
            (self.u, "GetParent", HANDLE, [HANDLE]),
            (self.u, "GetWindowLongW", LONG, [HANDLE, C.c_int]),
            (self.u, "SetThreadDpiAwarenessContext", HANDLE, [HANDLE]),
            (self.u, "GetDpiForWindow", C.c_uint, [HANDLE]),
            (self.u, "GetDpiForSystem", C.c_uint, []),
            (self.u, "SendMessageTimeoutW", LONG_PTR,
                 [HANDLE, C.c_uint, ULONG_PTR, LONG_PTR, C.c_uint, C.c_uint, C.POINTER(ULONG_PTR)]),
            (self.u, "PostMessageW", BOOL, [HANDLE, C.c_uint, ULONG_PTR, LONG_PTR]),
            (self.u, "GetWindowDC", HANDLE, [HANDLE]),
            (self.u, "ReleaseDC", C.c_int, [HANDLE, HANDLE]),
            (self.u, "PrintWindow", BOOL, [HANDLE, HANDLE, C.c_uint]),
            (self.g, "CreateCompatibleDC", HANDLE, [HANDLE]),
            (self.g, "CreateCompatibleBitmap", HANDLE, [HANDLE, C.c_int, C.c_int]),
            (self.g, "SelectObject", HANDLE, [HANDLE, HANDLE]),
            (self.g, "DeleteObject", BOOL, [HANDLE]),
            (self.g, "DeleteDC", BOOL, [HANDLE]),
            (self.g, "GetDIBits", C.c_int,
                 [HANDLE, HANDLE, C.c_uint, C.c_uint, HANDLE, HANDLE, C.c_uint]),
        ]
        for library, name, result, arguments in bindings:
            function = getattr(library, name)
            function.restype, function.argtypes = result, arguments
        # Physical bounds, without DPI virtualization in the observer. This changes
        # only this helper thread, never Windows display settings or the installer.
        self.check(self.u.SetThreadDpiAwarenessContext(HANDLE(-4)), "SetThreadDpiAwarenessContext")

    @staticmethod
    def check(value, operation):
        if not value:
            raise C.WinError(C.get_last_error(), operation)
        return value

    def owned(self, window: int, job: int) -> bool:
        if not self.u.IsWindow(window):
            return False
        pid = DWORD()
        self.u.GetWindowThreadProcessId(window, C.byref(pid))
        process = self.k.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not process:
            return False
        try:
            belongs = BOOL()
            return bool(self.k.IsProcessInJob(process, job, C.byref(belongs)) and belongs.value)
        finally:
            self.k.CloseHandle(process)

    def windows(self, job: int, parent: int | None = None) -> list[int]:
        result = []
        @self.callback
        def collect(window, unused):
            if self.owned(window, job):
                result.append(int(window))
            return True
        if parent is None:
            self.u.EnumWindows(collect, 0)
        else:
            self.u.EnumChildWindows(parent, collect, 0)
        return result

    def text(self, window: int) -> str:
        buffer = C.create_unicode_buffer(8192)
        result = ULONG_PTR()
        if not self.u.SendMessageTimeoutW(window, WM_GETTEXT, len(buffer), C.addressof(buffer),
                                         3, 500, C.byref(result)):
            return ""
        return buffer.value

    def checked(self, window: int) -> bool:
        result = ULONG_PTR()
        self.check(self.u.SendMessageTimeoutW(window, BM_GETCHECK, 0, 0, 3, 500, C.byref(result)),
                   "Read native checkbox state")
        if result.value not in (0, 1):
            raise AssertionError("Unexpected native checkbox state")
        return bool(result.value)

    def rect(self, window: int, client=False) -> list[int]:
        box = RECT()
        self.check((self.u.GetClientRect if client else self.u.GetWindowRect)(window, C.byref(box)),
                   "Get window bounds")
        if client:
            point = POINT()
            self.check(self.u.ClientToScreen(window, C.byref(point)), "ClientToScreen")
            return [point.x, point.y, point.x + box.right, point.y + box.bottom]
        return [box.left, box.top, box.right, box.bottom]

    def control(self, window: int) -> dict:
        name, pid = C.create_unicode_buffer(256), DWORD()
        self.u.GetClassNameW(window, name, len(name))
        self.u.GetWindowThreadProcessId(window, C.byref(pid))
        return {"hwnd": window, "pid": pid.value, "class": name.value,
                "text": self.text(window), "rect": self.rect(window),
                "visible": bool(self.u.IsWindowVisible(window)),
                "enabled": bool(self.u.IsWindowEnabled(window)),
                "control_id": self.u.GetDlgCtrlID(window),
                "parent": self.u.GetParent(window), "style": self.u.GetWindowLongW(window, -16)}

    def screenshot(self, window: int, output: Path) -> None:
        from PIL import Image
        left, top, right, bottom = self.rect(window)
        width, height = right - left, bottom - top
        if not (100 <= width <= 8192 and 100 <= height <= 8192):
            raise AssertionError("Unexpected installer capture dimensions")
        source = self.check(self.u.GetWindowDC(window), "GetWindowDC")
        target = bitmap = previous = None
        try:
            target = self.check(self.g.CreateCompatibleDC(source), "CreateCompatibleDC")
            bitmap = self.check(self.g.CreateCompatibleBitmap(source, width, height), "CreateCompatibleBitmap")
            previous = self.check(self.g.SelectObject(target, bitmap), "SelectObject")
            self.check(self.u.PrintWindow(window, target, 2), "PrintWindow")
            self.g.SelectObject(target, previous)
            previous = None
            header = BITMAPINFOHEADER()
            header.biSize, header.biWidth, header.biHeight = C.sizeof(header), width, -height
            header.biPlanes, header.biBitCount = 1, 32
            pixels = C.create_string_buffer(width * height * 4)
            if self.g.GetDIBits(target, bitmap, 0, height, pixels, C.byref(header), 0) != height:
                raise C.WinError(C.get_last_error(), "GetDIBits")
            image = Image.frombuffer("RGB", (width, height), pixels.raw, "raw", "BGRX", 0, 1)
            if all(low == high for low, high in image.getextrema()):
                raise AssertionError("PrintWindow returned a blank image")
            image.save(output)
        finally:
            if previous:
                self.g.SelectObject(target, previous)
            if bitmap:
                self.g.DeleteObject(bitmap)
            if target:
                self.g.DeleteDC(target)
            self.u.ReleaseDC(window, source)


class InstallerJob:
    def __init__(self, api: Native, arguments: list[str]):
        self.api, self.process, self.job, self.pid = api, None, None, None
        self.forced_cleanup = False
        self.job = api.check(api.k.CreateJobObjectW(None, None), "CreateJobObjectW")
        try:
            limit = JOB_EXTENDED_LIMIT()
            limit.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway
            api.check(api.k.SetInformationJobObject(self.job, 9, C.byref(limit), C.sizeof(limit)),
                      "SetInformationJobObject")
            startup, info = STARTUPINFO(), PROCESS_INFORMATION()
            startup.cb = C.sizeof(startup)
            command = C.create_unicode_buffer(subprocess.list2cmdline(arguments))
            api.check(api.k.CreateProcessW(arguments[0], command, None, None, False, 4,
                      None, str(ROOT), C.byref(startup), C.byref(info)), "CreateProcessW suspended")
            self.process, self.pid = info.hProcess, info.dwProcessId
            try:
                api.check(api.k.AssignProcessToJobObject(self.job, self.process), "AssignProcessToJobObject")
                if api.k.ResumeThread(info.hThread) == 0xFFFFFFFF:
                    raise C.WinError(C.get_last_error(), "ResumeThread")
            except BaseException:
                api.k.TerminateProcess(self.process, 99)  # Exact newly created, suspended process only
                raise
            finally:
                api.k.CloseHandle(info.hThread)
        except BaseException:
            self.close()
            raise

    def active(self) -> int:
        accounting = JOB_ACCOUNTING()
        self.api.check(self.api.k.QueryInformationJobObject(self.job, 1, C.byref(accounting),
                       C.sizeof(accounting), None), "QueryInformationJobObject")
        return accounting.ActiveProcesses

    def close(self) -> None:
        if self.job:
            if self.active():
                self.forced_cleanup = True
                self.api.k.TerminateJobObject(self.job, 99)
            self.api.k.CloseHandle(self.job)
            self.job = None
        if self.process:
            self.api.k.CloseHandle(self.process)
            self.process = None


def contains_rect(parent: list[int], child: list[int]) -> bool:
    return (parent[0] <= child[0] < child[2] <= parent[2]
            and parent[1] <= child[1] < child[3] <= parent[3])


def verify_logo_background(path: Path, values: dict, window_rect: list[int], client_rect: list[int]) -> dict:
    """Compare transparent PNG corners against the adjacent real wizard body."""
    from PIL import Image
    with Image.open(path) as source:
        image = source.convert("RGB")
    x = client_rect[0] - window_rect[0] + values["logo_left"]
    y = client_rect[1] - window_rect[1] + values["logo_top"]
    width, height = values["logo_width"], values["logo_height"]
    inset = max(1, min(width, height) // 30)
    reference = image.getpixel((x - 4, y + inset))
    corners = [image.getpixel((xx, yy)) for xx in (x + inset, x + width - 1 - inset)
               for yy in (y + inset, y + height - 1 - inset)]
    if any(max(abs(a - b) for a, b in zip(pixel, reference)) > 3 for pixel in corners):
        raise AssertionError(f"Brand PNG has a rectangular backing: {corners} versus body {reference}")
    return {"body_rgb": reference, "transparent_corner_rgb": corners, "passed": True}


def browse_and_cancel(api: Native, job: InstallerJob, values: dict, translated: dict, output: Path) -> dict:
    """Exercise the actual native browse callback, cancelling without path changes."""
    window, button, edit = values["window"], values["browse"], values["directory"]
    original = api.text(edit)
    if not api.owned(button, job.job) or not api.u.IsChild(window, button):
        raise AssertionError("Folder button is outside the owned wizard")
    api.check(api.u.PostMessageW(button, BM_CLICK, 0, 0), "Activate owned folder button")
    deadline, dialog = time.monotonic() + 10, None
    while time.monotonic() < deadline:
        candidates = [hwnd for hwnd in api.windows(job.job) if hwnd != window and api.u.IsWindowVisible(hwnd)
                      and caption(api.text(hwnd)) == caption(translated["BrowseDialogTitle"])]
        if len(candidates) == 1 and not api.u.IsWindowEnabled(window):
            dialog = candidates[0]
            break
        time.sleep(0.05)
    if dialog is None:
        raise AssertionError("Folder icon did not open the native translated directory chooser")
    api.screenshot(dialog, output)
    api.check(api.u.PostMessageW(dialog, WM_CLOSE, 0, 0), "Cancel owned directory chooser")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not api.u.IsWindowVisible(dialog) and api.u.IsWindowEnabled(window):
            if api.text(edit) != original:
                raise AssertionError("Cancelling the directory chooser changed the path")
            return {"passed": True, "dialog_caption": translated["BrowseDialogTitle"],
                    "path_unchanged": True, "screenshot": str(output)}
        time.sleep(0.05)
    raise TimeoutError("Owned directory chooser did not cancel")


def verify_layout(api: Native, job: InstallerJob, values: dict, translated: dict, desktop_selected=False) -> dict:
    required = {"page", "footer", "button_height", "primary_width", "secondary_width",
                "scale_x_100", "scale_y_100", "window", "directory", "desktop", "next", "cancel",
                "browse", "directory_page", "logo_left", "logo_top", "logo_width", "logo_height"}
    if not required <= values.keys():
        raise AssertionError("LUMA_UI telemetry is missing fields: " + str(sorted(required - values.keys())))
    window = values["window"]
    for key in ("window", "directory", "desktop", "next", "cancel", "browse", "directory_page"):
        handle = values[key]
        if not api.owned(handle, job.job) or not api.u.IsWindowVisible(handle):
            raise AssertionError(f"{key} is not a visible window owned by the installer job")
        if key != "window" and not api.u.IsChild(window, handle):
            raise AssertionError(f"{key} is not a child of the logged wizard")
    controls = [api.control(handle) for handle in api.windows(job.job, window)]
    by_handle = {item["hwnd"]: item for item in controls}
    next_button, cancel_button = by_handle[values["next"]], by_handle[values["cancel"]]
    desktop, directory = by_handle[values["desktop"]], by_handle[values["directory"]]
    browse = by_handle[values["browse"]]
    if browse["class"] != "TBitmapButton" or not browse["style"] & 0x10000:
        raise AssertionError("Folder icon is not a keyboard-focusable native bitmap button")
    if caption(browse["text"]) != caption(translated["ChangeLocation"]):
        raise AssertionError("Folder icon lost its translated accessible name")
    page_bounds = api.rect(values["directory_page"], client=True)
    for item in (desktop, directory, browse):
        if item["parent"] != values["directory_page"] or not contains_rect(page_bounds, item["rect"]):
            raise AssertionError("Configuration control extends beyond its actual notebook page")
    if directory["rect"][2] >= browse["rect"][0]:
        raise AssertionError("Folder button overlaps the editable installation path")
    for axis, size in (("x", browse["rect"][2] - browse["rect"][0]),
                       ("y", browse["rect"][3] - browse["rect"][1])):
        if abs(size - (38 * values[f"scale_{axis}_100"] + 50) // 100) > 1:
            raise AssertionError("Folder button hit target does not scale with native metrics")
    primary_captions = {caption(translated[key]) for key in ("ButtonInstall", "ButtonNext", "UpdateAction") if key in translated}
    if caption(next_button["text"]) not in primary_captions:
        raise AssertionError("Primary button has the wrong language/caption")
    if caption(cancel_button["text"]) != caption(translated["ButtonCancel"]):
        raise AssertionError("Cancel button has the wrong language/caption")
    if caption(desktop["text"]) != caption(translated["DesktopShortcut"]):
        raise AssertionError("Desktop checkbox is missing or has the wrong language/caption")
    desktop["checked"] = api.checked(values["desktop"])
    if desktop["checked"] != desktop_selected:
        raise AssertionError("Desktop checkbox does not reflect the explicit /TASKS selection")
    visible_text = "\n".join(item["text"] for item in controls if item["visible"])
    # Copy is read from the same checked-in localization source as the package.
    directory_labels = [value for key, value in translated.items()
                        if key in ("InstallFolder", "InstallDirectory", "InstallLocation", "DirectoryLabel",
                                   "InstallFolderLabel", "InstallDirectoryLabel", "DestinationLabel", "FolderLabel")]
    if directory_labels and not any(caption(value) in caption(visible_text) for value in directory_labels):
        raise AssertionError("Translated installation-directory label is not visible")
    for field, logical, axis in (("footer", 60, "y"), ("button_height", 36, "y"),
                                 ("primary_width", 112, "x"), ("secondary_width", 82, "x")):
        scale = values[f"scale_{axis}_100"]
        if not (50 <= scale <= 600):
            raise AssertionError("Implausible native font scaling")
        expected = (logical * scale + 50) // 100
        if abs(values[field] - expected) > 1:
            raise AssertionError(f"{field}: {values[field]} != Scale{axis.upper()}({logical}), expected approximately {expected}")
    client = api.rect(window, client=True)
    footer_top = client[3] - values["footer"]
    for item, width in ((next_button, values["primary_width"]), (cancel_button, values["secondary_width"])):
        rect = item["rect"]
        if rect[2] - rect[0] != width or rect[3] - rect[1] != values["button_height"]:
            raise AssertionError("Actual native button size disagrees with logged layout")
        if not (client[0] <= rect[0] < rect[2] <= client[2] and footer_top <= rect[1] < rect[3] <= client[3]):
            raise AssertionError("Native button is outside the footer/client area")
        if abs((rect[1] - footer_top) - (client[3] - rect[3])) > 1:
            raise AssertionError("Native button is not vertically centered in the footer")
    a, b = next_button["rect"], cancel_button["rect"]
    if max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]):
        raise AssertionError("Primary and cancel buttons overlap")
    footer_panels = [item for item in controls if item["visible"] and "panel" in item["class"].lower()
                     and item["rect"] == [client[0], footer_top, client[2], client[3]]]
    if not footer_panels:
        raise AssertionError("No native panel matches the logged 60-logical-unit footer")
    for item in (desktop, directory):
        rect = item["rect"]
        if not (client[0] <= rect[0] < rect[2] <= client[2] and client[1] <= rect[1] < rect[3] <= footer_top):
            raise AssertionError("Configuration control is outside the content area")
    return {"metrics": values, "window": api.control(window), "client_rect": client,
            "window_dpi": api.u.GetDpiForWindow(window), "system_dpi": api.u.GetDpiForSystem(),
            "controls": controls, "footer_panels": footer_panels,
            "translation_checks": ["primary", "cancel", "desktop", "folder_accessible_name"] + (["directory_label"] if directory_labels else [])}


def cancel(api: Native, job: InstallerJob, window: int, translated: dict, timeout=15) -> dict:
    if not api.owned(window, job.job):
        raise AssertionError("Refusing to close a window outside the installer job")
    api.check(api.u.PostMessageW(window, WM_CLOSE, 0, 0), "Close owned installer wizard")
    deadline, clicked = time.monotonic() + timeout, None
    expected_message = caption(translated["ExitSetupMessage"].split("%n")[0])
    while time.monotonic() < deadline:
        if not job.active():
            return {"confirmed": clicked is not None, "yes_button": clicked}
        for dialog in api.windows(job.job):
            if dialog == window or not api.u.IsWindowVisible(dialog):
                continue
            info = api.control(dialog)
            if info["class"] not in ("#32770", "TTaskDialogForm"):
                continue
            children = [api.control(handle) for handle in api.windows(job.job, dialog)]
            visible = [item for item in children if item["visible"]]
            text = caption(" ".join([info["text"]] + [item["text"] for item in visible]))
            if expected_message not in text:
                continue
            yes = [item for item in visible if item["enabled"] and "button" in item["class"].lower()
                   and caption(item["text"]) == caption(translated["ButtonYes"])]
            if clicked is None and len(yes) == 1:
                handle = yes[0]["hwnd"]
                if not api.owned(handle, job.job) or not api.u.IsChild(dialog, handle):
                    raise AssertionError("Cancellation Yes button lost its verified ownership")
                # Only the verified cancellation dialog's localized Yes. Never Next/Install.
                api.check(api.u.PostMessageW(handle, BM_CLICK, 0, 0), "Confirm installer cancellation")
                clicked = yes[0]
        time.sleep(0.1)
    raise TimeoutError("Installer did not finish native cancellation within 15 seconds")


def run_case(api: Native, installer: Path, language: str, output: Path, work: Path,
             variant: str, timeout: int) -> dict:
    name = f"{variant}-{language}"
    log, destination = output / f"{name}.log", work / name / "installed"
    if log.exists():
        log.unlink()  # Test-owned prior report only; logs cannot be mistaken for this run.
    destination.parent.mkdir(parents=True)
    row = {"language": language, "variant": variant, "passed": False,
           "installer": str(installer), "log": str(log), "screenshot": str(output / f"{name}.png"),
           "scope": "first configuration page only; installation is never started"}
    desktop_selected = variant == "native-desktop-selected"
    row["desktop_task_requested"] = desktop_selected
    task_argument = "/TASKS=desktopicon" if desktop_selected else "/TASKS="
    job = InstallerJob(api, [str(installer), "/SP-", f"/LANG={language}", "/NORESTART", task_argument,
                             f"/LOG={log}", f"/DIR={destination}"])
    translated, window = messages(language), None
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not job.active():
                raise AssertionError("Installer exited before configuration-page telemetry")
            candidates = parse_metrics(log)
            if candidates:
                values = candidates[-1]
                candidate = values.get("window", 0)
                if (api.owned(candidate, job.job) and api.u.IsWindowVisible(candidate)
                        and api.u.IsWindowVisible(values.get("directory", 0))):
                    window = candidate
                    time.sleep(0.3)
                    row.update(verify_layout(api, job, values, translated, desktop_selected))
                    api.screenshot(window, Path(row["screenshot"]))
                    row["logo_background"] = verify_logo_background(Path(row["screenshot"]), values,
                        row["window"]["rect"], row["client_rect"])
                    row["directory_chooser"] = browse_and_cancel(api, job, values, translated,
                        output / f"{name}-folder-dialog.png")
                    break
            time.sleep(0.1)
        else:
            raise TimeoutError(f"No visible logged configuration page within {timeout} seconds")
        row["cancellation"] = cancel(api, job, window, translated)
        if not row["cancellation"]["confirmed"]:
            raise AssertionError("Native cancellation confirmation was not observed")
        if destination.exists():
            raise AssertionError("Configuration-only test unexpectedly created the installation destination")
        row["passed"] = True
    except Exception as error:
        row["error"] = f"{type(error).__name__}: {error}"
        if window and job.active():
            try:
                if not Path(row["screenshot"]).exists():
                    api.screenshot(window, Path(row["screenshot"]))
                row["cancellation"] = cancel(api, job, window, translated)
            except Exception as cleanup_error:
                row["cleanup_error"] = f"{type(cleanup_error).__name__}: {cleanup_error}"
    finally:
        job.close()
        row["forced_process_cleanup"] = job.forced_cleanup
        if job.forced_cleanup:
            row["passed"] = False
        row["all_telemetry"] = parse_metrics(log)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--scaled-installer", type=Path,
                        help="Separate test-only DialogFontSize=18 package; not real OS-DPI evidence")
    parser.add_argument("--timeout", type=int, default=60, choices=range(10, 121), metavar="10..120")
    parser.add_argument("--_worker-language", choices=LANGUAGES, help=argparse.SUPPRESS)
    parser.add_argument("--_worker-variant", choices=("native", "font-scale-18", "native-desktop-selected"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise SystemExit("Refusing installer UI smoke outside an ephemeral GitHub-hosted Windows runner.")
    output = ROOT / "build/installer-ui"
    output.mkdir(parents=True, exist_ok=True)
    if args._worker_language:
        if not args._worker_variant:
            parser.error("A worker requires an explicit variant")
        api = Native()
        work = Path(tempfile.mkdtemp(prefix="luma-installer-ui-", dir=os.environ["RUNNER_TEMP"]))
        row = run_case(api, args.installer.resolve(strict=True), args._worker_language,
                       output, work, args._worker_variant, args.timeout)
        (output / f"{args._worker_variant}-{args._worker_language}.json").write_text(
            json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0 if row["passed"] else 1
    report = {"passed": False, "cases": [], "capture": "PrintWindow of verified installer HWND only",
              "safety": "No Install/Next clicks; only owned directory chooser/cancellation; private job assigned before initial thread resumes",
              "dpi_scope": "Actual runner/window DPI is recorded. Font-scale stress is not OS DPI testing."}
    try:
        variants = [("native", args.installer)]
        if args.scaled_installer:
            variants.append(("font-scale-18", args.scaled_installer))
        resolved = []
        for variant, path in variants:
            path = path.resolve(strict=True)
            if not path.is_file() or path.suffix.casefold() != ".exe":
                raise ValueError("Each installer must be an existing .exe")
            resolved.append((variant, path))
        if len(resolved) == 2 and resolved[0][1] == resolved[1][1]:
            raise ValueError("The font-scale stress test requires a separate installer output")
        report["installers"] = {variant: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                                for variant, path in resolved}
        for variant, path in resolved + [("native-desktop-selected", resolved[0][1])]:
            for language in (("en",) if variant == "native-desktop-selected" else LANGUAGES):
                name = f"{variant}-{language}"
                case_file = output / f"{name}.json"
                if case_file.exists():
                    case_file.unlink()
                # PrintWindow is synchronous, so give the complete native probe an
                # external hard timeout. Killing only this exact helper closes its
                # private job handle and terminates its installer descendants.
                command = [sys.executable, str(Path(__file__).resolve()), "--installer", str(path),
                           "--_worker-language", language, "--_worker-variant", variant,
                           "--timeout", str(args.timeout)]
                try:
                    result = subprocess.run(command, cwd=ROOT, timeout=args.timeout + 35,
                                            capture_output=True, text=True, errors="replace")
                    (output / f"{name}-process.log").write_text(result.stdout + result.stderr, encoding="utf-8")
                    if case_file.exists():
                        row = json.loads(case_file.read_text(encoding="utf-8"))
                        if result.returncode != 0:
                            row["passed"] = False
                    else:
                        row = {"passed": False, "language": language, "variant": variant,
                               "error": f"Native UI helper exited {result.returncode} without its report"}
                except subprocess.TimeoutExpired:
                    row = {"passed": False, "language": language, "variant": variant,
                           "error": "UI helper exceeded its hard timeout and was terminated; its private job closes automatically"}
                report["cases"].append(row)
                (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        report["passed"] = all(row["passed"] for row in report["cases"])
        if args.scaled_installer and report["passed"]:
            native = {row["language"]: row for row in report["cases"] if row["variant"] == "native"}
            for row in report["cases"]:
                if row["variant"] == "font-scale-18":
                    if row["metrics"]["scale_y_100"] <= native[row["language"]]["metrics"]["scale_y_100"]:
                        raise AssertionError("Font-scale package did not increase native dialog-font scaling")
    except Exception as error:
        report["passed"] = False
        report["error"] = f"{type(error).__name__}: {error}"
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(output / "report.json")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
