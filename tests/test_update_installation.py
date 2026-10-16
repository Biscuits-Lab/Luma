"""Portable tests of installed update and authenticated startup boundaries.

No installer, registry operation or Windows executable is run by these tests.
Real socket tests exercise only a temporary 127.0.0.1 startup acknowledgment.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from luma import update_installation as installation
from luma import update_probe as probe


def payload(root, version="0.1.3", digest="a" * 64):
    payload_id = version + "-" + digest[:24]
    directory = root / "versions" / payload_id
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "Luma.exe"
    executable.write_bytes(b"MZ-old-payload")
    (directory / "luma-install.json").write_text(json.dumps({
        "managed_install": True, "layout": "versions-v1", "version": version,
        "bundle_sha256": digest, "payload_id": payload_id,
    }), encoding="utf-8")
    return executable


def setup_file(root, version="0.1.4"):
    root.mkdir(parents=True, exist_ok=True)
    setup = root / f"Luma-{version}-windows-x64-setup.exe"
    setup.write_bytes(b"MZ-verified-official-setup-test-double")
    return setup, hashlib.sha256(setup.read_bytes()).hexdigest()


@pytest.fixture
def managed(tmp_path):
    root = tmp_path / "Custom Luma Location"
    executable = payload(root)
    return installation.detect_installation(executable, frozen=True, platform="win32")


class SetupProcess:
    def __init__(self, code=0):
        self.code = code
        self.pid = 12345
        self.finished = threading.Event()
        if code is not None:
            self.finished.set()

    def poll(self):
        return self.code

    def wait(self):
        self.finished.wait()
        return self.code

    def finish(self, code):
        self.code = code
        self.finished.set()


@pytest.fixture(autouse=True)
def synthetic_process_image_boundary(monkeypatch):
    # Protocol tests use synthetic Popen objects, never Windows process handles.
    # Do not send those test doubles to QueryFullProcessImageNameW on native CI.
    # Real process objects still take the strict production OS-query path, which
    # is exercised below and by smoke_update_managed's actual frozen GUI.
    original = probe.process_executable
    monkeypatch.setattr(probe, "process_executable", lambda process:
                        None if isinstance(process, SetupProcess) else original(process))


@pytest.fixture(autouse=True)
def isolated_setup_processes():
    yield
    for process in tuple(installation._RUNNING_SETUPS.values()):
        if hasattr(process, "finish"):
            process.finish(5)
    with installation._SETUP_LOCK:
        installation._RUNNING_SETUPS.clear()
        for lease in installation._SETUP_LEASES.values():
            lease.close()
        installation._SETUP_LEASES.clear()


def apply(managed, tmp_path, popen, **kwargs):
    setup, digest = setup_file(tmp_path / "downloads")
    return installation.apply_verified_setup(setup, digest, "0.1.4", managed, popen=popen, **kwargs)


def test_classification_separates_source_unsupported_portable_managed(tmp_path, managed):
    assert installation.detect_installation(Path("python"), frozen=False, platform="win32").mode == "source"
    assert installation.detect_installation(Path("Luma"), frozen=True, platform="linux").mode == "unsupported"
    portable = tmp_path / "Luma.exe"
    portable.write_bytes(b"MZ-portable")
    assert installation.detect_installation(portable, frozen=True, platform="win32").mode == "portable"
    assert managed.mode == "managed"
    assert managed.version == "0.1.3"
    assert managed.root == managed.executable.parent.parent.parent


@pytest.mark.parametrize("content", ["{", "{}", '[]', '"portable"', '{"managed_install":false}', "x" * 4097])
def test_malformed_marker_never_falls_back_to_portable(tmp_path, content):
    executable = tmp_path / "Luma.exe"
    executable.write_bytes(b"MZ")
    (tmp_path / "luma-install.json").write_text(content)
    with pytest.raises(installation.InstallationError):
        installation.detect_installation(executable, frozen=True, platform="win32")


def test_missing_managed_marker_and_moved_marker_fail_closed(managed, tmp_path):
    marker = managed.executable.parent / "luma-install.json"
    metadata = marker.read_bytes()
    marker.unlink()
    with pytest.raises(installation.InstallationError):
        installation.detect_installation(managed.executable, frozen=True, platform="win32")
    (tmp_path / "luma-install.json").write_bytes(metadata)
    (tmp_path / "Luma.exe").write_bytes(b"MZ")
    with pytest.raises(installation.InstallationError):
        installation.detect_installation(tmp_path / "Luma.exe", frozen=True, platform="win32")


def test_symlink_to_managed_root_or_marker_fails_closed(tmp_path, managed):
    link = tmp_path / "linked"
    try:
        link.symlink_to(managed.root, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not allow unprivileged symlinks")
    with pytest.raises(installation.InstallationError):
        installation.detect_installation(link / managed.executable.relative_to(managed.root), frozen=True, platform="win32")
    marker = managed.executable.parent / "luma-install.json"
    data = marker.read_bytes()
    marker.unlink()
    real = tmp_path / "marker.json"
    real.write_bytes(data)
    marker.symlink_to(real)
    with pytest.raises(installation.InstallationError):
        installation.detect_installation(managed.executable, frozen=True, platform="win32")


def test_setup_uses_original_custom_root_and_preserves_all_existing_files(tmp_path, managed):
    image = managed.root / "photo.exr"
    settings = managed.root / "settings.json"
    dll = managed.executable.parent / "thumbnail.dll"
    for path in (image, settings, dll):
        path.write_bytes(b"preserve-exactly")
    before = {p: p.read_bytes() for p in (image, settings, dll, managed.executable)}
    commands = []
    def launch(command, **kwargs):
        commands.append((command, kwargs))
        payload(managed.root, "0.1.4", "b" * 64)
        return SetupProcess()
    result = apply(managed, tmp_path, launch)
    command, kwargs = commands[0]
    assert command[1:] == ["/SP-", "/SILENT", "/NORESTART", "/RESTARTEXITCODE=11",
                           "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", f"/DIR={managed.root}"]
    assert "/VERYSILENT" not in command
    assert not any(arg.startswith(("/TASKS", "/MERGETASKS", "/FORCECLOSE")) for arg in command)
    assert kwargs["shell"] is False and kwargs["close_fds"] is True
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert result.previous_executable == managed.executable
    assert result.executable == managed.root / "versions" / ("0.1.4-" + "b" * 24) / "Luma.exe"
    assert result.root == managed.root
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("code", [1, 3, 4, 6, 7, 8, 10, 11, 3010, -1])
def test_nonzero_setup_exit_never_reports_success_even_if_payload_exists(tmp_path, managed, code):
    def launch(*args, **kwargs):
        payload(managed.root, "0.1.4", "b" * 64)
        return SetupProcess(code)
    with pytest.raises(installation.InstallationError, match="exit code"):
        apply(managed, tmp_path, launch)
    assert managed.executable.is_file()


@pytest.mark.parametrize("code", [2, 5])
def test_native_setup_cancellation_is_distinct(tmp_path, managed, code):
    with pytest.raises(installation.InstallationCancelled):
        apply(managed, tmp_path, lambda *a, **k: SetupProcess(code))
    assert managed.executable.is_file()


def test_cancel_before_launch_never_starts_setup(tmp_path, managed):
    with pytest.raises(installation.InstallationCancelled):
        apply(managed, tmp_path, lambda *a, **k: pytest.fail("must not launch"), cancelled=lambda: True)


def test_cancel_during_setup_waits_for_completion_without_killing_it(tmp_path, managed):
    process = SetupProcess(None)
    cancelled = threading.Event()
    def launch(*args, **kwargs):
        cancelled.set()
        threading.Timer(0.03, lambda: process.finish(0)).start()
        return process
    with pytest.raises(installation.InstallationCancelled):
        apply(managed, tmp_path, launch, cancelled=cancelled.is_set, timeout=1)
    assert process.poll() == 0
    assert managed.executable.is_file()


def test_timeout_keeps_guard_and_prevents_second_setup_then_releases(tmp_path, managed, monkeypatch):
    process = SetupProcess(None)
    leases = []
    class Lease:
        def __init__(self, key):
            self.closed = False
            leases.append(self)
        def close(self):
            self.closed = True
    monkeypatch.setattr(installation, "_SetupLease", Lease)
    with pytest.raises(installation.InstallationPending, match="may still be running"):
        apply(managed, tmp_path, lambda *a, **k: process, timeout=0.03)
    assert not leases[0].closed
    with pytest.raises(installation.InstallationPending, match="still running"):
        apply(managed, tmp_path, lambda *a, **k: pytest.fail("parallel installer"))
    assert len(leases) == 1
    process.finish(5)
    deadline = time.monotonic() + 2
    while not leases[0].closed and time.monotonic() < deadline:
        time.sleep(0.005)
    assert leases[0].closed
    with pytest.raises(installation.InstallationCancelled):
        apply(managed, tmp_path, lambda *a, **k: SetupProcess(2))
    assert len(leases) == 2 and leases[1].closed


@pytest.mark.parametrize("mutation", ["checksum", "name", "version", "root", "marker", "not_pe", "portable"])
def test_changed_or_invalid_inputs_are_rejected_before_execution(tmp_path, managed, mutation):
    setup, digest = setup_file(tmp_path / "downloads")
    version, context = "0.1.4", managed
    if mutation == "checksum":
        setup.write_bytes(b"MZ-tampered")
    elif mutation == "name":
        renamed = setup.with_name("cmd.exe")
        setup.rename(renamed)
        setup = renamed
    elif mutation == "version":
        version = "0.1.4 /DIR=foreign"
    elif mutation == "root":
        context = installation.InstallationContext("managed", managed.executable, tmp_path, managed.version)
    elif mutation == "marker":
        (managed.executable.parent / "luma-install.json").write_text("{}")
    elif mutation == "not_pe":
        setup.write_bytes(b"not an exe")
        digest = hashlib.sha256(setup.read_bytes()).hexdigest()
    elif mutation == "portable":
        context = installation.InstallationContext("portable", managed.executable)
    with pytest.raises(installation.InstallationError):
        installation.apply_verified_setup(setup, digest, version, context,
                                          popen=lambda *a, **k: pytest.fail("unsafe launch"))


@pytest.mark.parametrize("version", ["0.1.3", "0.1.2"])
def test_same_version_and_downgrade_never_run(tmp_path, managed, version):
    setup, digest = setup_file(tmp_path / "downloads", version)
    with pytest.raises(installation.InstallationError, match="newer"):
        installation.apply_verified_setup(setup, digest, version, managed,
                                          popen=lambda *a, **k: pytest.fail("downgrade"))


@pytest.mark.parametrize("outcome", ["missing", "foreign_root", "ambiguous", "malformed"])
def test_exit_zero_requires_one_valid_target_in_original_root(tmp_path, managed, outcome):
    def launch(*args, **kwargs):
        if outcome == "foreign_root":
            payload(tmp_path / "Other Luma", "0.1.4")
        if outcome in ("ambiguous", "malformed"):
            new = payload(managed.root, "0.1.4", "b" * 64)
            if outcome == "ambiguous":
                payload(managed.root, "0.1.4", "c" * 64)
            else:
                (new.parent / "luma-install.json").write_text("{}")
        return SetupProcess()
    with pytest.raises(installation.InstallationError):
        apply(managed, tmp_path, launch)


def test_popen_error_releases_setup_lease(tmp_path, managed, monkeypatch):
    closed = []
    monkeypatch.setattr(installation, "_SetupLease", lambda key: SimpleNamespace(close=lambda: closed.append(True)))
    def fail(*args, **kwargs):
        raise OSError("denied")
    with pytest.raises(installation.InstallationError, match="could not be started"):
        apply(managed, tmp_path, fail)
    assert closed == [True]


def portable_executable(tmp_path):
    executable = tmp_path / "Luma.exe"
    executable.write_bytes(b"MZ-test")
    return executable


def ack_payload(actual_executable, actual_nonce, **changes):
    data = {"protocol": 1, "nonce": actual_nonce, "version": "0.1.4", "executable": str(actual_executable),
            "pid": 12345, "frozen": True}
    data.update(changes)
    return json.dumps(data).encode()


def socket_popen(executable, *, change=None, code=None, packet=None, hold=False):
    process = SetupProcess(code)
    calls = []
    def launch(command, **kwargs):
        calls.append((command, kwargs))
        def report():
            with socket.create_connection(("127.0.0.1", int(command[2])), timeout=2) as connection:
                message = packet if packet is not None else ack_payload(executable, command[4], **(change or {}))
                connection.sendall(message)
                if hold:
                    time.sleep(0.2)
        threading.Thread(target=report, daemon=True).start()
        return process
    return launch, calls, process


def test_real_loopback_ack_validates_new_version_path_pid_and_closes_listener(tmp_path):
    executable = portable_executable(tmp_path)
    launch, calls, process = socket_popen(executable)
    result = probe.launch_verified_update(executable, "0.1.4", popen=launch, timeout=2)
    assert result == probe.LaunchAcknowledgment("0.1.4", executable, process.pid)
    command, kwargs = calls[0]
    assert command[0] == str(executable)
    assert command[1] == "--update-startup-port" and command[3] == "--update-startup-nonce"
    assert len(command[4]) == 64
    assert kwargs["shell"] is False and kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", int(command[2])), timeout=0.1)


@pytest.mark.parametrize("changes", [{"version": "0.1.3"}, {"pid": 1}, {"pid": True}, {"nonce": "0" * 64},
                                      {"protocol": True}, {"protocol": 2}, {"frozen": False}, {"extra": "field"},
                                      {"executable": "/foreign/Luma.exe"}])
def test_startup_identity_mismatch_never_reports_success(tmp_path, changes):
    executable = portable_executable(tmp_path)
    launch, _, _ = socket_popen(executable, change=changes)
    with pytest.raises(probe.LaunchError):
        probe.launch_verified_update(executable, "0.1.4", popen=launch, timeout=2)
    assert executable.is_file()


@pytest.mark.parametrize("packet", [b"{", b"[]", b"", b"x" * (probe.MAX_ACK_BYTES + 1), b'{"nonce":"a","nonce":"b"}'])
def test_malformed_oversized_or_duplicate_ack_fails_closed(tmp_path, packet):
    executable = portable_executable(tmp_path)
    launch, _, _ = socket_popen(executable, packet=packet)
    with pytest.raises(probe.LaunchError):
        probe.launch_verified_update(executable, "0.1.4", popen=launch, timeout=2)


def test_live_process_without_ack_is_not_success(tmp_path):
    executable = portable_executable(tmp_path)
    with pytest.raises(probe.LaunchError, match="in time"):
        probe.launch_verified_update(executable, "0.1.4", popen=lambda *a, **k: SetupProcess(None), timeout=0.03)


def test_partial_ack_cannot_hold_listener_beyond_deadline(tmp_path):
    executable = portable_executable(tmp_path)
    launch, _, _ = socket_popen(executable, packet=b'{"protocol":', hold=True)
    with pytest.raises(probe.LaunchError, match="timed out"):
        probe.launch_verified_update(executable, "0.1.4", popen=launch, timeout=0.03)


def test_exited_candidate_does_not_authorize_old_app_exit(tmp_path):
    executable = portable_executable(tmp_path)
    with pytest.raises(probe.LaunchError, match="exited"):
        probe.launch_verified_update(executable, "0.1.4", popen=lambda *a, **k: SetupProcess(0), timeout=1)


def test_cancel_before_launch_does_not_start_candidate(tmp_path):
    with pytest.raises(probe.LaunchCancelled):
        probe.launch_verified_update(portable_executable(tmp_path), "0.1.4", cancelled=lambda: True,
                                     popen=lambda *a, **k: pytest.fail("cancelled launch"))


@pytest.mark.parametrize("port,nonce", [(80, "a" * 64), (65536, "a" * 64), (True, "a" * 64),
                                       ("127.0.0.1:1024", "a" * 64), (1024, "a" * 63),
                                       (1024, "x" * 64), (1024, "a" * 64 + "\n")])
def test_probe_endpoint_is_strict(port, nonce):
    with pytest.raises(probe.LaunchError):
        probe.validate_startup_endpoint(port, nonce)


def test_child_reports_its_actual_embedded_identity(monkeypatch, tmp_path):
    import luma
    executable = portable_executable(tmp_path)
    monkeypatch.setattr(probe.sys, "frozen", True, raising=False)
    monkeypatch.setattr(probe.sys, "executable", str(executable))
    monkeypatch.setattr(luma, "__version__", "0.1.4")
    nonce = "a" * 64
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        worker = probe.acknowledge_startup(listener.getsockname()[1], nonce)
        connection, address = listener.accept()
        with connection:
            data = b""
            while block := connection.recv(1024):
                data += block
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert json.loads(data) == {"protocol": 1, "nonce": nonce, "version": "0.1.4", "executable": str(executable),
                                "pid": os.getpid(), "frozen": True}
    assert address[0] == "127.0.0.1"


def test_source_process_cannot_ack_as_frozen(monkeypatch):
    monkeypatch.setattr(probe.sys, "frozen", False, raising=False)
    assert probe._send_startup_ack(12345, "a" * 64) is False


def test_independent_windows_image_mismatch_rejects_valid_self_report(tmp_path, monkeypatch):
    executable = portable_executable(tmp_path)
    foreign = tmp_path / "foreign" / "Luma.exe"
    foreign.parent.mkdir()
    foreign.write_bytes(b"MZ-foreign")
    monkeypatch.setattr(probe, "process_executable", lambda process: foreign)
    launch, _, _ = socket_popen(executable)
    with pytest.raises(probe.LaunchError, match="Windows process image"):
        probe.launch_verified_update(executable, "0.1.4", popen=launch, timeout=2)


def test_independent_process_image_is_in_acknowledgment(tmp_path, monkeypatch):
    executable = portable_executable(tmp_path)
    monkeypatch.setattr(probe, "process_executable", lambda process: executable)
    launch, _, _ = socket_popen(executable)
    acknowledgment = probe.launch_verified_update(executable, "0.1.4", popen=launch, timeout=2)
    assert acknowledgment.os_executable == executable


def fake_dll_api(original, *, clear=True, restore=True):
    calls = []
    state = {"directory": original}
    def get_directory(size, buffer):
        buffer.value = state["directory"]
        return len(state["directory"])
    def set_directory(directory):
        calls.append(directory)
        okay = clear if directory is None else restore
        if okay:
            state["directory"] = directory or ""
        return okay
    return SimpleNamespace(GetDllDirectoryW=get_directory, SetDllDirectoryW=set_directory), state, calls


def test_setup_spawn_resets_and_restores_exact_windows_dll_directory(monkeypatch):
    api, state, calls = fake_dll_api(r"C:\Luma older payload\_internal")
    monkeypatch.setattr(installation.sys, "platform", "win32")
    monkeypatch.setattr(installation, "_windows_dll_api", lambda: api)
    with installation._setup_dll_search_path():
        assert state["directory"] == ""
    assert calls == [None, r"C:\Luma older payload\_internal"]
    assert state["directory"] == r"C:\Luma older payload\_internal"


def test_setup_spawn_restores_search_directory_even_when_popen_fails(monkeypatch):
    api, state, calls = fake_dll_api(r"C:\Old\_internal")
    monkeypatch.setattr(installation.sys, "platform", "win32")
    monkeypatch.setattr(installation, "_windows_dll_api", lambda: api)
    with pytest.raises(OSError):
        with installation._setup_dll_search_path():
            raise OSError("CreateProcess failed")
    assert state["directory"] == r"C:\Old\_internal"
    assert calls == [None, r"C:\Old\_internal"]


def test_empty_dll_search_setting_is_not_changed(monkeypatch):
    api, _, calls = fake_dll_api("")
    monkeypatch.setattr(installation.sys, "platform", "win32")
    monkeypatch.setattr(installation, "_windows_dll_api", lambda: api)
    with installation._setup_dll_search_path():
        pass
    assert calls == []


def test_dll_reset_failure_prevents_native_spawn(monkeypatch):
    api, state, calls = fake_dll_api(r"C:\Old\_internal", clear=False)
    monkeypatch.setattr(installation.sys, "platform", "win32")
    monkeypatch.setattr(installation, "_windows_dll_api", lambda: api)
    with pytest.raises(installation.InstallationError, match="could not be cleared"):
        with installation._setup_dll_search_path():
            pytest.fail("must not launch with inherited old DLL path")
    assert state["directory"] == r"C:\Old\_internal"


def test_dll_restore_failure_after_spawn_keeps_pending_guard(tmp_path, managed, monkeypatch):
    api, _, _ = fake_dll_api(r"C:\Old\_internal", restore=False)
    process = SetupProcess(None)
    monkeypatch.setattr(installation.sys, "platform", "win32")
    monkeypatch.setattr(installation, "_windows_dll_api", lambda: api)
    monkeypatch.setattr(installation, "_SetupLease", lambda key: SimpleNamespace(close=lambda: None))
    with pytest.raises(installation.InstallationPending, match="could not be restored"):
        apply(managed, tmp_path, lambda *a, **k: process)
    assert process in installation._RUNNING_SETUPS.values()
    with pytest.raises(installation.InstallationPending, match="still running"):
        apply(managed, tmp_path, lambda *a, **k: pytest.fail("parallel setup"))
    process.finish(5)


def test_windows_process_query_uses_actual_created_handle(monkeypatch, tmp_path):
    import ctypes
    executable = portable_executable(tmp_path)
    calls = []
    def query(handle, flags, buffer, length):
        calls.append((handle, flags))
        buffer.value = str(executable)
        return True
    api = SimpleNamespace(QueryFullProcessImageNameW=query)
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: api, raising=False)
    assert probe.process_executable(SimpleNamespace(_handle=42)) == executable
    assert calls == [(42, 0)]


@pytest.mark.parametrize("has_handle", [False, True])
def test_windows_process_query_fails_closed_without_handle_or_os_result(monkeypatch, has_handle):
    import ctypes
    calls = []
    def query(*args):
        calls.append(args)
        return False
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: SimpleNamespace(QueryFullProcessImageNameW=query), raising=False)
    process = SimpleNamespace(_handle=42) if has_handle else SimpleNamespace()
    with pytest.raises(probe.LaunchError, match="Windows could not verify"):
        probe.process_executable(process)
    assert bool(calls) is has_handle


def test_synthetic_protocol_process_cannot_claim_independent_os_evidence():
    assert probe.process_executable(SetupProcess(None)) is None
