"""Read-only Windows Shell discovery probe for an already registered Luma.

SHAssocEnumHandlers(ASSOC_FILTER_NONE) proves the installed executable is in
the Shell's associated-handler collection used by Open With. IsRecommended
is diagnostic only: neither result guarantees the personalized short submenu
or substitutes for visual Windows 10/11 chooser QA. This script never registers,
invokes, promotes, or makes a handler the default.

API contract and ABI:
https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-shassocenumhandlers
https://learn.microsoft.com/windows/win32/api/shobjidl_core/nn-shobjidl_core-iassochandler
"""
from __future__ import annotations

import argparse
import ctypes
import json
import ntpath
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAX_HANDLERS = 256
HRESULT = ctypes.c_int32
ULONG = ctypes.c_uint32
POINTER = ctypes.c_void_p
S_OK, S_FALSE = 0, 1


def _check_hr(result: int, operation: str) -> None:
    if result < 0:
        raise OSError(f"{operation} failed: HRESULT 0x{result & 0xffffffff:08X}")


def _method(interface, slot, result_type, *argument_types):
    # IUnknown occupies slots 0..2. SDK IEnumAssocHandlers::Next is slot 3;
    # IAssocHandler GetName/GetUIName/IsRecommended are slots 3/4/6.
    table = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(POINTER))).contents
    return ctypes.WINFUNCTYPE(result_type, POINTER, *argument_types)(table[slot])


def _release(interface) -> None:
    if interface:
        _method(interface, 2, ULONG)(interface)


class ShellHandlers:
    """Own COM initialization and only the read-only association methods."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("Native Open With discovery requires Windows.")
        self.ole = ctypes.WinDLL("ole32")
        self.ole.CoInitializeEx.argtypes = [POINTER, ULONG]
        self.ole.CoInitializeEx.restype = HRESULT
        self.ole.CoUninitialize.argtypes = []
        self.ole.CoUninitialize.restype = None
        self.ole.CoTaskMemFree.argtypes = [POINTER]
        self.ole.CoTaskMemFree.restype = None
        self.shell = ctypes.WinDLL("shell32")
        self.shell.SHAssocEnumHandlers.argtypes = [ctypes.c_wchar_p, ctypes.c_int,
                                                  ctypes.POINTER(POINTER)]
        self.shell.SHAssocEnumHandlers.restype = HRESULT

    def __enter__(self):
        # COINIT_APARTMENTTHREADED. The CLI runs in a fresh, bounded subprocess.
        _check_hr(self.ole.CoInitializeEx(None, 2), "CoInitializeEx")
        return self

    def __exit__(self, *unused):
        self.ole.CoUninitialize()

    def _string(self, handler, slot, label):
        value = POINTER()
        try:
            hr = _method(handler, slot, HRESULT, ctypes.POINTER(POINTER))(
                handler, ctypes.byref(value))
            _check_hr(hr, label)
            return ctypes.wstring_at(value) if value else ""
        finally:
            if value:
                self.ole.CoTaskMemFree(value)

    def enumerate(self, extension: str) -> list[dict]:
        enumerator = POINTER()
        handlers = []
        try:
            # ASSOC_FILTER_NONE = 0: all handlers, not only recommended ones.
            _check_hr(self.shell.SHAssocEnumHandlers(extension, 0,
                                                     ctypes.byref(enumerator)),
                      "SHAssocEnumHandlers")
            if not enumerator:
                raise OSError("Shell returned no association enumerator.")
            next_handler = _method(enumerator, 3, HRESULT, ULONG,
                                   ctypes.POINTER(POINTER), ctypes.POINTER(ULONG))
            for _ in range(MAX_HANDLERS):
                handler, fetched = POINTER(), ULONG()
                try:
                    hr = next_handler(enumerator, 1, ctypes.byref(handler),
                                      ctypes.byref(fetched))
                    _check_hr(hr, "IEnumAssocHandlers.Next")
                    if hr not in (S_OK, S_FALSE) or fetched.value not in (0, 1):
                        raise OSError("Shell returned an invalid enumeration result.")
                    if not fetched.value:
                        if hr != S_FALSE or handler:
                            raise OSError("Shell returned an invalid end of enumeration.")
                        return handlers
                    if not handler:
                        raise OSError("Shell returned an empty association handler.")
                    record = {}
                    for field, slot in (("executable", 3), ("display_name", 4)):
                        try:
                            record[field] = self._string(handler, slot, field)
                        except OSError as error:
                            record[field] = None
                            record[field + "_error"] = str(error)
                    recommended = _method(handler, 6, HRESULT)(handler)
                    record["recommended"] = (recommended == S_OK
                                               if recommended in (S_OK, S_FALSE) else None)
                    if record["recommended"] is None:
                        record["recommended_hresult"] = f"0x{recommended & 0xffffffff:08X}"
                    handlers.append(record)
                finally:
                    _release(handler)
            raise OSError(f"Shell enumeration exceeded the {MAX_HANDLERS}-handler bound.")
        finally:
            _release(enumerator)


def discovery_report(executable: str, extensions, enumerate_handlers) -> dict:
    """Match the exact installed path, not a same-name stale/portable copy."""
    expected = ntpath.normcase(ntpath.normpath(executable))
    rows = []
    for extension in extensions:
        row = {"extension": extension, "found": False}
        try:
            handlers = enumerate_handlers(extension)
            row["handlers"] = handlers
            matches = [handler for handler in handlers
                       if handler.get("executable") and
                       ntpath.normcase(ntpath.normpath(handler["executable"])) == expected]
            row["luma_handlers"] = matches
            row["found"] = any(handler.get("display_name") == "Luma" for handler in matches)
        except OSError as error:
            row["error"] = str(error)
        rows.append(row)
    return {
        "passed": bool(rows) and all(row["found"] for row in rows),
        "executable": executable,
        "api": "SHAssocEnumHandlers / ASSOC_FILTER_NONE",
        "scope": "Native associated-handler discovery; not a visual or short-menu guarantee",
        "extensions": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"passed": False}
    try:
        executable = args.executable.resolve(strict=True)
        if not executable.is_file() or executable.name.casefold() != "luma.exe":
            raise ValueError("An existing Luma.exe is required.")
        from luma.shell_integration import EXTENSIONS
        with ShellHandlers() as shell:
            report = discovery_report(str(executable), EXTENSIONS, shell.enumerate)
    except (OSError, ValueError) as error:
        report["error"] = f"{type(error).__name__}: {error}"
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
