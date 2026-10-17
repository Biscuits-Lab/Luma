"""Portable harness regressions; only Windows CI proves native launcher behavior."""
import ctypes
from ctypes import wintypes
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

    def __init__(self, *, button=True, post=True, enumerate=True):
        self.ok_control = self.button if button else None
        self.post_result = post
        self.enum_result = enumerate
        self.posted = []
        self.owners = {self.dialog: 42, 11: 99, 12: 42, 13: 42}
        self.titles = {self.dialog: "Luma could not start", 11: "Luma could not start",
                       12: "Unrelated window", 13: "Luma could not start",
                       self.button: "确定", 21: ERROR_TEXT}

    def EnumWindows(self, callback, parameter):
        if not self.enum_result:
            return False
        for hwnd in self.owners:
            assert callback(hwnd, parameter)
        return True

    def GetWindowThreadProcessId(self, hwnd, owner):
        ctypes.cast(owner, ctypes.POINTER(wintypes.DWORD)).contents.value = self.owners[hwnd]
        return 1

    def IsWindowVisible(self, hwnd):
        return hwnd != 13

    def GetWindowTextW(self, hwnd, buffer, size):
        buffer.value = self.titles[hwnd][:size - 1]
        return len(buffer.value)

    def EnumChildWindows(self, hwnd, callback, parameter):
        assert hwnd == self.dialog
        for child in (self.button, 21):
            assert callback(child, parameter)

    def GetDlgItem(self, hwnd, control):
        assert (hwnd, control) == (self.dialog, 1)
        return self.ok_control

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
        api.acknowledged_dialogs = set()
        return api
    return create


def test_dialog_api_uses_pointer_sized_control_handle(monkeypatch):
    libraries = {"kernel32": Mock(), "user32": Mock()}
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_: libraries[name], raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    api = launcher.Windows()
    assert api.user.GetDlgItem.argtypes == [wintypes.HWND, ctypes.c_int]
    assert api.user.GetDlgItem.restype is wintypes.HWND
    assert api.user.PostMessageW.argtypes[-1] is wintypes.LPARAM
    assert api.user.PostMessageW.restype is wintypes.BOOL


def test_acknowledgment_targets_real_ok_control_and_owned_visible_dialog(dialog_harness):
    api = dialog_harness()
    assert api.dismiss_error(42) == ["确定\n" + ERROR_TEXT]
    assert api.user.posted == [(api.user.dialog, 0x0111, 1, api.user.button)]
    assert api.dialogs == ["确定\n" + ERROR_TEXT]
    # Posting is asynchronous: a still-enumerated window must not be posted again
    # or inspected while its first acknowledgment destroys its controls.
    assert api.dismiss_error(42) == []
    assert len(api.user.posted) == 1


def test_missing_ok_control_fails_without_posting(dialog_harness):
    api = dialog_harness(button=False)
    with pytest.raises(AssertionError, match="error dialog has no OK control"):
        api.dismiss_error(42)
    assert api.user.posted == []
    assert api.dialogs == []


def test_failed_acknowledgment_is_not_recorded_as_success(dialog_harness):
    api = dialog_harness(post=False)
    with pytest.raises(AssertionError, match="Could not acknowledge launcher error dialog: 5"):
        api.dismiss_error(42)
    assert api.dialogs == []
    assert api.acknowledged_dialogs == set()


def test_failed_window_enumeration_is_reported(dialog_harness):
    api = dialog_harness(enumerate=False)
    with pytest.raises(AssertionError, match="Could not enumerate launcher error dialogs: 5"):
        api.dismiss_error(42)


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
