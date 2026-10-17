"""Portable harness regressions; only Windows CI proves native launcher behavior."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from native.launcher import test_launcher_windows as launcher


ERROR_TEXT = "Invalid installation. No other version was started. Run the official Luma Setup to repair it."


class DialogAPI:
    """Emulate only the Win32 calls used by error-dialog acknowledgment."""

    dialog = 0x123400000001
    button = 0x123400000002

    def __init__(self, *, button=True, post=True, enumerate=True, control_id=2):
        self.post_result = post
        self.enum_result = enumerate
        self.posted = []
        self.owners = {self.dialog: 42, 11: 99, 12: 42, 13: 42}
        self.titles = {self.dialog: "Luma could not start", 11: "Luma could not start",
                       12: "Unrelated window", 13: "Luma could not start"}
        self.controls = {21: {"class": "Static", "control_id": 0xffff, "parent_hwnd": self.dialog,
                             "pid": 42, "title": ERROR_TEXT, "visible": True, "enabled": True}}
        if button:
            self.controls[self.button] = {"class": "Button", "control_id": control_id,
                "parent_hwnd": self.dialog, "pid": 42, "title": "确定", "visible": True, "enabled": True}

    def EnumWindows(self, callback, parameter):
        if not self.enum_result:
            return False
        for hwnd in self.owners:
            assert callback(hwnd, parameter)
        return True

    def GetWindowThreadProcessId(self, hwnd, owner):
        pid = self.controls[hwnd]["pid"] if hwnd in self.controls else self.owners[hwnd]
        ctypes.cast(owner, ctypes.POINTER(wintypes.DWORD)).contents.value = pid
        return 1

    def IsWindowVisible(self, hwnd):
        return self.controls[hwnd]["visible"] if hwnd in self.controls else hwnd != 13

    def IsWindowEnabled(self, hwnd):
        return self.controls[hwnd]["enabled"]

    def GetWindowTextW(self, hwnd, buffer, size):
        title = self.controls[hwnd]["title"] if hwnd in self.controls else self.titles[hwnd]
        buffer.value = title[:size - 1]
        return len(buffer.value)

    def GetClassNameW(self, hwnd, buffer, size):
        buffer.value = self.controls[hwnd]["class"][:size - 1]
        return len(buffer.value)

    def EnumChildWindows(self, hwnd, callback, parameter):
        assert hwnd == self.dialog
        for child in self.controls:
            assert callback(child, parameter)

    def GetDlgCtrlID(self, hwnd):
        return self.controls[hwnd]["control_id"]

    def GetParent(self, hwnd):
        return self.controls[hwnd]["parent_hwnd"]

    def PostMessageW(self, *args):
        self.posted.append(args)
        return self.post_result


@pytest.fixture
def dialog_harness(monkeypatch):
    # get_last_error is Windows-only; this is a fake-API unit test on any OS.
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)

    def create(**kwargs):
        api = launcher.Windows.__new__(launcher.Windows)
        api.user = DialogAPI(**kwargs)
        api.enum_callback = lambda callback: callback
        api.dialogs = []
        api.dialog_observations = []
        api.acknowledged_dialogs = set()
        return api
    return create


def test_dialog_api_uses_pointer_sized_control_handle(monkeypatch):
    libraries = {"kernel32": Mock(), "user32": Mock()}
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_: libraries[name], raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    api = launcher.Windows()
    assert api.user.GetParent.argtypes == [wintypes.HWND]
    assert api.user.GetParent.restype is wintypes.HWND
    assert api.user.GetDlgCtrlID.argtypes == [wintypes.HWND]
    assert api.user.GetDlgCtrlID.restype is ctypes.c_int
    assert api.user.PostMessageW.argtypes[-1] is wintypes.LPARAM
    assert api.user.PostMessageW.restype is wintypes.BOOL


@pytest.mark.parametrize("control_id", (1, 2, 100, 0xffff))
def test_acknowledgment_discovers_real_id_and_owned_visible_dialog(dialog_harness, control_id):
    api = dialog_harness(control_id=control_id)
    assert api.dismiss_error(42) == [ERROR_TEXT + "\n确定"]
    assert api.user.posted == [(api.user.dialog, 0x0111, control_id, api.user.button)]
    assert api.dialogs == [ERROR_TEXT + "\n确定"]
    observation = api.dialog_observations[0]
    assert observation["controls"] == [{"hwnd": hwnd, **control} for hwnd, control in api.user.controls.items()]
    assert observation["acknowledgment"] == {"parent_hwnd": api.user.dialog,
        "control_id": control_id, "button_hwnd": api.user.button, "posted": True}
    # Posting is asynchronous: a still-enumerated window must not be posted again
    # or inspected while its first acknowledgment destroys its controls.
    assert api.dismiss_error(42) == []
    assert len(api.user.posted) == 1
    assert len(api.dialog_observations) == 1


def test_missing_ok_control_fails_without_posting(dialog_harness):
    api = dialog_harness(button=False)
    with pytest.raises(AssertionError, match="expected one visible enabled native button"):
        api.dismiss_error(42)
    assert api.user.posted == []
    assert api.dialogs == []
    assert api.dialog_observations[0]["controls"][0]["title"] == ERROR_TEXT


def test_nested_button_notifies_its_actual_owned_parent(dialog_harness):
    api = dialog_harness(control_id=2)
    container = 0x123400000003
    api.user.controls[container] = {"class": "Pane", "control_id": 100, "parent_hwnd": api.user.dialog,
                                  "pid": 42, "title": "", "visible": True, "enabled": True}
    api.user.controls[api.user.button]["parent_hwnd"] = container
    api.dismiss_error(42)
    assert api.user.posted == [(container, 0x0111, 2, api.user.button)]


def test_ambiguous_buttons_fail_without_guessing(dialog_harness):
    api = dialog_harness()
    api.user.controls[22] = dict(api.user.controls[api.user.button], control_id=1, title="OK")
    with pytest.raises(AssertionError, match="expected one visible enabled native button"):
        api.dismiss_error(42)
    assert api.user.posted == []
    assert len(api.dialog_observations[0]["controls"]) == 3


@pytest.mark.parametrize("state", ({"visible": False}, {"enabled": False}, {"class": "Static"}))
def test_button_caption_alone_does_not_authorize_acknowledgment(dialog_harness, state):
    api = dialog_harness()
    api.user.controls[api.user.button].update(title="OK", **state)
    with pytest.raises(AssertionError, match="expected one visible enabled native button"):
        api.dismiss_error(42)
    assert api.user.posted == []


@pytest.mark.parametrize("control_id", (0, -1, 0x10000))
def test_invalid_command_id_is_reported_without_posting(dialog_harness, control_id):
    api = dialog_harness(control_id=control_id)
    with pytest.raises(AssertionError, match="has no valid command ID"):
        api.dismiss_error(42)
    assert api.user.posted == []


@pytest.mark.parametrize("changes, expected", [
    ({"pid": 99}, "belongs to another process"),
    ({"parent_hwnd": 99}, "outside its owned subtree"),
    ({"parent_hwnd": None}, "outside its owned subtree"),
    ({"parent_hwnd": DialogAPI.button}, "outside its owned subtree"),
])
def test_button_and_parent_must_remain_owned(dialog_harness, changes, expected):
    api = dialog_harness()
    api.user.controls[api.user.button].update(changes)
    with pytest.raises(AssertionError, match=expected):
        api.dismiss_error(42)
    assert api.user.posted == []


def test_failed_acknowledgment_is_not_recorded_as_success(dialog_harness):
    api = dialog_harness(post=False)
    with pytest.raises(AssertionError, match="Could not acknowledge launcher error dialog: 5"):
        api.dismiss_error(42)
    assert api.dialogs == []
    assert api.acknowledged_dialogs == set()
    assert api.dialog_observations[0]["acknowledgment"]["posted"] is False


def test_failed_window_enumeration_is_reported(dialog_harness):
    api = dialog_harness(enumerate=False)
    with pytest.raises(AssertionError, match="Could not enumerate launcher error dialogs: 5"):
        api.dismiss_error(42)


def test_native_report_keeps_control_diagnostics_when_case_fails(monkeypatch, tmp_path):
    api = SimpleNamespace(dialog_observations=[], verify_resources=lambda _: {})
    details = {"pid": 42, "hwnd": 123, "controls": [{"class": "Button", "control_id": 2,
               "parent_hwnd": 123, "hwnd": 456, "pid": 42, "visible": True, "enabled": True, "title": "确定"}]}

    def fail_fixture(*_):
        api.dialog_observations.append(details)
        raise AssertionError("Native dialog probe failed")

    monkeypatch.setattr(launcher, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(launcher, "Windows", lambda: api)
    monkeypatch.setattr(launcher, "Fixture", fail_fixture)
    executable = tmp_path / "Luma.exe"
    executable.write_bytes(b"test-only placeholder")
    report_path = tmp_path / "reports" / "launcher.json"
    args = SimpleNamespace(launcher=executable, fixture=executable, report=report_path)
    with pytest.raises(AssertionError, match="Native dialog probe failed"):
        launcher.run(args)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["checks"] == []
    assert report["dialog_observations"] == [details]


class FakeProcess:
    pid = 42

    def __init__(self, exit_code):
        self.exit_code = exit_code
        self.killed = False

    def poll(self):
        return self.exit_code

    def wait(self, timeout):
        if self.killed:
            return -9
        if self.exit_code is None:
            raise subprocess.TimeoutExpired(["Luma.exe"], timeout)
        return self.exit_code

    def kill(self):
        self.killed = True


@pytest.fixture
def negative_launch(monkeypatch, tmp_path):
    def create(*, exit_code=120, errors=(ERROR_TEXT,), payload=False):
        fixture = launcher.Fixture.__new__(launcher.Fixture)
        fixture.root = tmp_path / "Luma"
        fixture.root.mkdir()
        fixture.evidence = []
        kernel = SimpleNamespace(CreateEventW=Mock(return_value=1), SetEvent=Mock(), CloseHandle=Mock())
        fixture.api = SimpleNamespace(kernel=kernel, dismiss_error=Mock(return_value=list(errors)))
        process = FakeProcess(exit_code)

        def start(args, **_):
            if payload:
                Path(args[2]).write_text("{}", encoding="utf-8")
            return process

        ticks = iter((0, 1, 20))
        monkeypatch.setattr(launcher, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
        monkeypatch.setattr(launcher, "subprocess", SimpleNamespace(Popen=start, TimeoutExpired=subprocess.TimeoutExpired))
        return fixture, process
    return create


def test_negative_case_requires_real_exit_120_and_dialog(negative_launch):
    fixture, process = negative_launch()
    with fixture.launch("empty_pointer", expected=None) as data:
        assert data is None
    assert fixture.evidence == [{"case": "empty_pointer", "result": "passed",
                                 "exit_code": 120, "error": [ERROR_TEXT]}]
    assert not process.killed
    fixture.api.kernel.SetEvent.assert_called_once_with(1)
    fixture.api.kernel.CloseHandle.assert_called_once_with(1)


@pytest.mark.parametrize("exit_code", (0, 1, 119, 121))
def test_negative_case_rejects_wrong_exit_code(negative_launch, exit_code):
    fixture, _ = negative_launch(exit_code=exit_code)
    with pytest.raises(AssertionError, match="failure exit code missing"):
        with fixture.launch("empty_pointer", expected=None):
            pass
    assert fixture.evidence == []


def test_negative_case_rejects_any_started_payload(negative_launch):
    fixture, _ = negative_launch(payload=True)
    with pytest.raises(AssertionError, match="invalid installation launched a payload"):
        with fixture.launch("empty_pointer", expected=None):
            pass
    assert fixture.evidence == []


def test_cleanup_does_not_mask_missing_honest_error(negative_launch):
    fixture, process = negative_launch(exit_code=None, errors=("Something failed",))
    with pytest.raises(AssertionError, match="honest error dialog missing") as caught:
        with fixture.launch("empty_pointer", expected=None):
            pass
    assert process.killed
    assert "cleanup timed out" in caught.value.__notes__[0]
    assert fixture.evidence == []
    fixture.api.kernel.CloseHandle.assert_called_once_with(1)


def test_queued_acknowledgment_cannot_pass_without_launcher_exit(negative_launch):
    fixture, process = negative_launch(exit_code=None)
    with pytest.raises(AssertionError, match="did not exit after error dialog acknowledgment") as caught:
        with fixture.launch("empty_pointer", expected=None):
            pass
    assert ERROR_TEXT in str(caught.value)
    assert process.killed
    assert fixture.evidence == []
    fixture.api.kernel.CloseHandle.assert_called_once_with(1)
