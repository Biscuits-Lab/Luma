"""Verify DWM requests/fallbacks without claiming Windows visual coverage."""
import ctypes
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent

from luma import chrome
from luma.app import ViewerWindow


@pytest.fixture
def fake_windows(monkeypatch):
    monkeypatch.setattr(chrome.sys, 'platform', 'win32')
    monkeypatch.setattr(chrome.sys, 'getwindowsversion',
                        lambda: SimpleNamespace(build=22631), raising=False)


def test_non_windows_never_loads_native_dll(monkeypatch):
    monkeypatch.setattr(chrome.sys, 'platform', 'linux')
    monkeypatch.setattr(chrome, '_dwm_functions', lambda: pytest.fail('Do not load DWM'))
    assert not chrome.apply_native_corners(123)


def test_windows_10_retains_existing_chrome(fake_windows, monkeypatch):
    monkeypatch.setattr(chrome.sys, 'getwindowsversion', lambda: SimpleNamespace(build=19045))
    monkeypatch.setattr(chrome, '_dwm_functions', lambda: pytest.fail('Do not change Win10 frame'))
    assert not chrome.apply_native_corners(123)


@pytest.mark.parametrize('rounded,preference,margin', [(True, 2, 1), (False, 1, 0)])
def test_windows_11_requests_native_corner_and_frame(fake_windows, monkeypatch, rounded, preference, margin):
    calls = []
    def attribute(hwnd, attr, value, size):
        calls.append(('attribute', hwnd, attr, ctypes.cast(value, ctypes.POINTER(ctypes.c_int32)).contents.value, size))
        return 0
    def extend(hwnd, value):
        margins = ctypes.cast(value, ctypes.POINTER(chrome._Margins)).contents
        calls.append(('extend', hwnd, margins.left, margins.right, margins.top, margins.bottom))
        return 0
    monkeypatch.setattr(chrome, '_dwm_functions', lambda: (attribute, extend))
    # An HWND may use more than 32 bits.
    hwnd = 0x12345678ABCD
    assert chrome.apply_native_corners(hwnd, rounded)
    assert calls == [('attribute', hwnd, 33, preference, 4),
                     ('extend', hwnd, margin, margin, margin, margin)]


@pytest.mark.parametrize('hresult', [-2147024809, 0x80070057, -2147467259])
def test_failed_corner_hresult_does_not_extend_frame(fake_windows, monkeypatch, hresult):
    monkeypatch.setattr(chrome, '_dwm_functions', lambda: (
        lambda *args: hresult, lambda *args: pytest.fail('Failed opt-in must leave frame alone')))
    assert not chrome.apply_native_corners(123)


def test_failed_frame_hresult_reported(fake_windows, monkeypatch):
    monkeypatch.setattr(chrome, '_dwm_functions', lambda: (lambda *args: 0, lambda *args: -1))
    assert not chrome.apply_native_corners(123)


@pytest.mark.parametrize('error', [OSError('DWM unavailable'), AttributeError('No entry point')])
def test_missing_native_support_is_safe(fake_windows, monkeypatch, error):
    def unavailable():
        raise error
    monkeypatch.setattr(chrome, '_dwm_functions', unavailable)
    assert not chrome.apply_native_corners(123)


def test_dwm_ctypes_signatures_are_pointer_safe(monkeypatch):
    class Function:
        pass
    attribute, extend = Function(), Function()
    monkeypatch.setattr(chrome.ctypes, 'WinDLL',
                        lambda *args, **kwargs: SimpleNamespace(
                            DwmSetWindowAttribute=attribute, DwmExtendFrameIntoClientArea=extend), raising=False)
    chrome._dwm_functions.cache_clear()
    try:
        assert chrome._dwm_functions() == (attribute, extend)
        assert attribute.argtypes == [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        assert attribute.restype is ctypes.c_int32
        assert extend.argtypes == [ctypes.c_void_p, ctypes.POINTER(chrome._Margins)]
        assert extend.restype is ctypes.c_int32
        assert ctypes.sizeof(chrome._Margins) == 16
    finally:
        chrome._dwm_functions.cache_clear()


def test_window_state_requests_native_policy_without_masks(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(chrome, 'apply_native_corners', lambda hwnd, rounded: calls.append((hwnd, rounded)) or True)
    window = ViewerWindow()
    window.show()
    qapp.processEvents()
    try:
        window.chrome._update_native_corners()
        assert calls[-1] == (int(window.winId()), True)
        assert window.chrome.native_corners_applied
        window.showMaximized()
        qapp.processEvents()
        window.chrome._update_native_corners()
        assert calls[-1][1] is False
        window.showNormal()
        qapp.processEvents()
        window.chrome._update_native_corners()
        assert calls[-1][1] is True
        window.showFullScreen()
        qapp.processEvents()
        window.chrome._update_native_corners()
        assert calls[-1][1] is False
        assert window.mask().isEmpty()
        assert window.titlebar.height() == 34
        assert window.viewbar.height() == 36
    finally:
        window.close()
    previous = list(calls)
    window.chrome._update_native_corners()
    assert calls == previous


def test_native_state_updates_are_batched_after_events(qapp, monkeypatch):
    window = ViewerWindow()
    window.show()
    qapp.processEvents()
    calls = []
    try:
        monkeypatch.setattr(chrome.sys, 'platform', 'win32')
        monkeypatch.setattr(chrome, 'apply_native_corners', lambda hwnd, rounded: calls.append((hwnd, rounded)) or True)
        for _ in range(5):
            window.chrome.eventFilter(window, QEvent(QEvent.Type.ActivationChange))
        assert calls == []
        qapp.processEvents()
        assert len(calls) == 1
    finally:
        window.close()
