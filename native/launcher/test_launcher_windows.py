"""Execute the actual MSVC launcher and fixture payload on disposable Windows CI.

This suite records process identity from Windows, exercises real CreateProcessW
argument roundtrips, missing/corrupt pointer failures, hashes, hardlinks/junctions,
atomic pointer replacement, and an old payload still alive during activation.
The synthetic helper is test-only and never included in a release bundle.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise AssertionError(message)


def same_path(left, right):
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


def atomic_write(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Windows:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        k, u = self.kernel, self.user
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
        k.GetProcessTimes.restype = wintypes.BOOL
        k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateEventW.restype = wintypes.HANDLE
        k.SetEvent.argtypes = [wintypes.HANDLE]
        k.SetEvent.restype = wintypes.BOOL
        self.enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [self.enum_callback, wintypes.LPARAM]
        u.EnumWindows.restype = wintypes.BOOL
        u.EnumChildWindows.argtypes = [wintypes.HWND, self.enum_callback, wintypes.LPARAM]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.dialogs = []

    def verify_resources(self, executable):
        version = ctypes.WinDLL("version", use_last_error=True)
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        version.VerQueryValueW.restype = wintypes.BOOL
        unused = wintypes.DWORD()
        size = version.GetFileVersionInfoSizeW(str(executable), ctypes.byref(unused))
        require(size, "Stable launcher version resource missing")
        buffer = ctypes.create_string_buffer(size)
        require(version.GetFileVersionInfoW(str(executable), 0, size, buffer), "Could not read launcher version resources")
        values = {}
        for key, expected in {"FileVersion": "1.0.0.0", "ProductVersion": "1.0.0.0", "ProductName": "Luma", "CompanyName": "Biscuits Lab", "OriginalFilename": "Luma.exe"}.items():
            result = ctypes.c_void_p()
            length = wintypes.UINT()
            require(version.VerQueryValueW(buffer, "\\StringFileInfo\\040904B0\\" + key, ctypes.byref(result), ctypes.byref(length)), f"Missing resource: {key}")
            values[key] = ctypes.wstring_at(result.value)
            require(values[key] == expected, f"Unexpected launcher resource {key}: {values[key]}")
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        shell.ExtractIconExW.restype = wintypes.UINT
        require(shell.ExtractIconExW(str(executable), -1, None, None, 0) > 0, "Stable launcher Luma icon is missing")
        return values

    def identity(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        require(handle, f"OpenProcess failed for fixture PID {pid}")
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            require(self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)), "OS process image query failed")
            creation, exit_time, kernel, user = (wintypes.FILETIME() for _ in range(4))
            require(self.kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)), "OS process times query failed")
            return {"pid": pid, "os_image": buffer.value, "creation_time": (creation.dwHighDateTime << 32) | creation.dwLowDateTime}
        finally:
            self.kernel.CloseHandle(handle)

    def parent_pid(self, pid):
        # Native layout includes an ULONG_PTR; a packed or 32-bit structure would
        # silently query the wrong parent on the required Windows x64 runner.
        class ProcessEntry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", wintypes.WCHAR * 260)]
        api = self.kernel
        api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        snapshot = api.CreateToolhelp32Snapshot(2, 0)
        require(snapshot not in (None, ctypes.c_void_p(-1).value), "Process snapshot failed")
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = api.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                if entry.th32ProcessID == pid:
                    return entry.th32ParentProcessID
                more = api.Process32NextW(snapshot, ctypes.byref(entry))
            raise AssertionError("Payload missing from OS process snapshot")
        finally:
            api.CloseHandle(snapshot)

    def dismiss_error(self, pid):
        found = []
        @self.enum_callback
        def visit(hwnd, _):
            owner = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            title = ctypes.create_unicode_buffer(2048)
            self.user.GetWindowTextW(hwnd, title, len(title))
            if owner.value != pid or title.value != "Luma could not start":
                return True
            texts = []
            @self.enum_callback
            def child(child_hwnd, _):
                text = ctypes.create_unicode_buffer(4096)
                self.user.GetWindowTextW(child_hwnd, text, len(text))
                if text.value:
                    texts.append(text.value)
                return True
            self.user.EnumChildWindows(hwnd, child, 0)
            found.append("\n".join(texts))
            self.user.PostMessageW(hwnd, 0x0111, 1, 0)  # WM_COMMAND, IDOK
            return True
        self.user.EnumWindows(visit, 0)
        self.dialogs.extend(found)
        return found


def pointer(payload):
    return ("LUMA-ACTIVE-1\n" + payload["id"] + "\n" + payload["bundle"] + "\n" + payload["exe_sha256"] + "\n").encode("ascii")


class Fixture:
    def __init__(self, directory, launcher, helper, api, evidence):
        self.root = directory / "Luma space 测试"
        self.root.mkdir(parents=True)
        self.launcher = self.root / "Luma.exe"
        shutil.copyfile(launcher, self.launcher)
        (self.root / "launcher-owner.txt").write_bytes(("LUMA-LAUNCHER-1\n" + digest(self.launcher) + "\n").encode("ascii"))
        self.api, self.evidence = api, evidence
        self.payloads = []
        for version, letter in (("0.1.3", "a"), ("0.1.4", "b")):
            bundle = letter * 64
            payload_id = version + "-" + bundle[:24]
            path = self.root / "versions" / payload_id
            path.mkdir(parents=True)
            shutil.copyfile(helper, path / "Luma.exe")
            metadata = {"managed_install": True, "layout": "versions-v1", "version": version,
                        "payload_id": payload_id, "bundle_sha256": bundle}
            (path / "luma-install.json").write_text(json.dumps(metadata), encoding="utf-8")
            self.payloads.append({"id": payload_id, "bundle": bundle, "path": path, "exe_sha256": digest(path / "Luma.exe")})
        self.activate(0)

    def activate(self, index):
        atomic_write(self.root / "active-payload.txt", pointer(self.payloads[index]))

    @contextmanager
    def launch(self, label, extra=(), expected=0, root=None):
        root = root or self.root
        report = self.root.parent / (uuid.uuid4().hex + ".json")
        event_name = "Local\\Luma.LauncherTest." + uuid.uuid4().hex
        event = self.api.kernel.CreateEventW(None, True, False, event_name)
        require(event, "Fixture event creation failed")
        args = ["--launcher-test-report", str(report), "--launcher-test-event", event_name, *extra]
        process = subprocess.Popen([str(root / "Luma.exe"), *args], cwd=self.root.parent)
        try:
            deadline = time.monotonic() + 12
            data = None
            errors = []
            while time.monotonic() < deadline:
                errors.extend(self.api.dismiss_error(process.pid))
                if report.exists():
                    try:
                        data = json.loads(report.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, PermissionError):
                        pass
                    if data is not None:
                        break
                if process.poll() is not None:
                    break
                time.sleep(0.02)
            if expected is None:
                require(data is None, f"{label}: invalid installation launched a payload")
                require(errors and any("No other version was started" in error for error in errors), f"{label}: honest error dialog missing")
                require(process.wait(timeout=3) == 120, f"{label}: failure exit code missing")
                self.evidence.append({"case": label, "result": "passed", "exit_code": 120, "error": errors})
                yield None
            else:
                require(data is not None, f"{label}: payload did not start; dialogs={errors}")
                require(process.poll() is None, f"{label}: root launcher must remain alive during acknowledgment")
                child = self.api.identity(data["pid"])
                parent = self.api.identity(process.pid)
                os_parent = self.api.parent_pid(data["pid"])
                payload = self.payloads[expected] if isinstance(expected, int) else next(
                    item for item in self.payloads if same_path(item["path"] / "Luma.exe", child["os_image"]))
                require(same_path(child["os_image"], payload["path"] / "Luma.exe"), f"{label}: wrong OS payload image")
                require(same_path(parent["os_image"], root / "Luma.exe"), f"{label}: wrong OS launcher image")
                require(data["parent_pid"] == os_parent == process.pid, f"{label}: payload is not immediate root child")
                require(child["creation_time"] >= parent["creation_time"], f"{label}: payload predates launcher")
                require(same_path(data["cwd"], payload["path"]), f"{label}: wrong fixed working directory")
                require(data["argv"][1:] == args, f"{label}: argument quoting changed argv")
                self.evidence.append({"case": label, "result": "passed", "launcher": parent, "payload": child,
                                      "os_parent_pid": os_parent, "cwd": data["cwd"], "argv": data["argv"]})
                yield data
        finally:
            self.api.kernel.SetEvent(event)
            try:
                result = process.wait(timeout=5)
                if expected is not None and data is not None:
                    require(result == 0, f"{label}: root failed to propagate successful payload exit {result}")
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
                raise AssertionError(f"{label}: launcher failed to exit after payload")
            finally:
                self.api.kernel.CloseHandle(event)


def verify_deletion_fence(api, source: Path, directory: Path, evidence: list) -> None:
    """Prove the exact final Inno guard against a disposable native image."""
    directory.mkdir()
    executable = directory / "Luma.exe"
    shutil.copyfile(source, executable)
    kernel = api.kernel
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.DeleteFileW.argtypes = [wintypes.LPCWSTR]
    kernel.DeleteFileW.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(executable), 0xC0000000, 4, None, 3, 0x80, None)
    require(handle not in (None, ctypes.c_void_p(-1).value),
            f"Could not retain native deletion guard: {ctypes.get_last_error()}")
    blocked_error = None
    try:
        try:
            process = subprocess.Popen([str(executable)], cwd=directory)
        except OSError as error:
            blocked_error = error.winerror
            require(blocked_error in (5, 32), f"Unexpected guarded CreateProcess error: {error}")
        else:
            # This isolated copy has no owner or payload; it cannot launch a
            # viewer. If image creation unexpectedly succeeds, fail and close
            # only this known test-owned error process rather than hanging CI.
            process.kill()
            process.wait(timeout=5)
            raise AssertionError("Retained deletion guard allowed CreateProcess")
        require(kernel.DeleteFileW(str(executable)),
                f"FILE_SHARE_DELETE guard prevented deletion: {ctypes.get_last_error()}")
    finally:
        require(kernel.CloseHandle(handle), "Could not release native deletion guard")
    require(not executable.exists(), "Guarded deleted image remained after handle release")
    evidence.append({"case": "retained_uninstall_guard_blocks_launch_allows_delete",
        "result": "passed", "desired_access": "GENERIC_READ|GENERIC_WRITE",
        "share_mode": "FILE_SHARE_DELETE", "create_process_winerror": blocked_error,
        "delete_while_guarded": True, "absent_after_release": True})


def run(args):
    require(sys.platform == "win32", "This is a native Windows test; do not label Linux tests as Windows evidence")
    api = Windows()
    report = {"platform": sys.platform, "launcher_sha256": digest(args.launcher), "native_executed": True, "checks": []}
    evidence = report["checks"]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        report["resources"] = api.verify_resources(args.launcher)
        with tempfile.TemporaryDirectory(prefix="luma-native-launcher-") as temporary:
            base = Path(temporary).resolve()
            fixture = Fixture(base, args.launcher, args.fixture, api, evidence)
            verify_deletion_fence(api, args.launcher, base / "deletion-guard", evidence)
            unusual = ["", "space and 中文.exr", 'embedded"quote', "C:\\tail\\", "slashes\\\\\"quote", "tab\tnewline\nname", "%PATH%", "a&b|c", "--update-startup-port", "12345", "--update-startup-nonce", "a" * 64]
            with fixture.launch("unicode_spaces_quotes_hidden_args", unusual):
                pass
            with fixture.launch("old_payload_kept_alive", ["old.exr"], expected=0) as old:
                old_sha = digest(fixture.payloads[0]["path"] / "Luma.exe")
                fixture.activate(1)
                with fixture.launch("same_root_selects_new_payload", ["new.exr"], expected=1):
                    require(same_path(api.identity(old["pid"])["os_image"], fixture.payloads[0]["path"] / "Luma.exe"), "Old payload stopped during pointer upgrade")
                    require(digest(fixture.payloads[0]["path"] / "Luma.exe") == old_sha, "Old immutable payload changed")
            fixture.activate(0)
            stop = threading.Event()
            failures = []
            def replace_pointer():
                try:
                    while not stop.is_set():
                        fixture.activate(0)
                        fixture.activate(1)
                        time.sleep(0.001)
                except BaseException as error:
                    failures.append(repr(error))
            writer = threading.Thread(target=replace_pointer)
            writer.start()
            try:
                for index in range(8):
                    with fixture.launch(f"atomic_pointer_snapshot_{index}", expected="either"):
                        pass
            finally:
                stop.set()
                writer.join(timeout=5)
            require(not writer.is_alive() and not failures, f"Atomic pointer writer failed: {failures}")
            fixture.activate(0)
            active = fixture.root / "active-payload.txt"
            valid = active.read_bytes()
            attacks = {
                "empty_pointer": b"", "oversized_pointer": b"x" * 513,
                "truncated_pointer": valid[:-1], "extra_pointer_fields": valid + b"extra\n",
                "traversal_pointer": valid.replace(fixture.payloads[0]["id"].encode(), b"../outside"),
                "absolute_pointer": valid.replace(fixture.payloads[0]["id"].encode(), b"C:\\outside"),
                "pointer_ads": valid.replace(fixture.payloads[0]["id"].encode(), b"payload:stream"),
                "wrong_executable_hash": valid.replace(fixture.payloads[0]["exe_sha256"].encode(), b"f" * 64),
                "unknown_protocol": valid.replace(b"LUMA-ACTIVE-1", b"LUMA-ACTIVE-2"),
            }
            for label, content in attacks.items():
                atomic_write(active, content)
                with fixture.launch(label, expected=None):
                    pass
            active.unlink()
            with fixture.launch("missing_pointer_no_old_version_fallback", expected=None):
                pass
            atomic_write(active, valid)
            metadata = fixture.payloads[0]["path"] / "luma-install.json"
            saved = metadata.read_bytes()
            metadata.write_bytes(saved.replace(b'"version": "0.1.3"', b'"version": "9.9.9"'))
            with fixture.launch("metadata_identity_mismatch", expected=None):
                pass
            metadata.write_bytes(saved)
            exe = fixture.payloads[0]["path"] / "Luma.exe"
            saved_exe = exe.read_bytes()
            with exe.open("ab") as stream:
                stream.write(b"changed")
            with fixture.launch("payload_corruption", expected=None):
                pass
            exe.write_bytes(saved_exe)
            hardlink = fixture.root.parent / "hardlinked-payload.exe"
            os.link(exe, hardlink)
            try:
                with fixture.launch("payload_hardlink_rejected", expected=None):
                    pass
            finally:
                hardlink.unlink()
            owner = fixture.root / "launcher-owner.txt"
            saved_owner = owner.read_bytes()
            owner.write_bytes(saved_owner.replace(digest(fixture.launcher).encode(), b"f" * 64))
            with fixture.launch("root_owner_hash_mismatch", expected=None):
                pass
            owner.write_bytes(saved_owner)
            # Directory junctions do not need developer mode or symbolic-link privilege.
            versions = fixture.root / "versions"
            moved = fixture.root / "retained-versions"
            versions.rename(moved)
            command = [str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"), "/d", "/c", "mklink", "/J", str(versions), str(moved)]
            result = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False)
            require(result.returncode == 0, f"Could not create required junction fixture: {result.stdout} {result.stderr}")
            try:
                with fixture.launch("versions_junction_rejected", expected=None):
                    pass
            finally:
                versions.rmdir()
                moved.rename(versions)
            root_link = base / "root-link"
            result = subprocess.run(command[:5] + [str(root_link), str(fixture.root)], capture_output=True, text=True, errors="replace", check=False)
            require(result.returncode == 0, f"Could not create root junction fixture: {result.stdout} {result.stderr}")
            try:
                with fixture.launch("root_ancestor_junction_rejected", expected=None, root=root_link):
                    pass
            finally:
                root_link.rmdir()
            # Source executable bytes are immutable and unchanged by the reader.
            require(digest(fixture.launcher) == report["launcher_sha256"], "Root launcher changed during tests")
        report["status"] = "passed"
        print(f"PASS: {len(evidence)} native Windows launcher cases; report={args.report}")
    except BaseException as error:
        report.update(status="failed", error=repr(error))
        raise
    finally:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    run(parser.parse_args())
