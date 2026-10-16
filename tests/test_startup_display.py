"""First-frame ordering, including the path used by Windows file associations."""
import threading

from PIL import Image
import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication

from luma import app as app_module
from luma.app import ViewerWindow
from test_ui import wait_for


class PaintCounter(QObject):
    def __init__(self, widget):
        super().__init__(widget)
        self.paints = 0
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            self.paints += 1
        return False


@pytest.mark.parametrize('target', ['image', 'folder', 'none'])
def test_main_selects_initial_page_before_show(qapp, tmp_path, monkeypatch, target):
    """A shell association supplies the same single path argument as the CLI."""
    image = tmp_path / '中文 image with spaces.png'
    Image.new('RGB', (80, 60), 'red').save(image)
    windows = []
    before_show = []

    class TrackedWindow(ViewerWindow):
        def __init__(self):
            super().__init__()
            self.welcome_paints = PaintCounter(self.welcome)
            windows.append(self)

        def show(self):
            before_show.append(self.stack.currentWidget() is self.canvas)
            super().show()
            # A platform can paint during show(), before main reaches its timer.
            qapp.processEvents()

    monkeypatch.setattr(app_module, 'ViewerWindow', TrackedWindow)
    from luma import shell_settings
    monkeypatch.setattr(shell_settings, 'start_automatic_registration', lambda window: None)

    def run_event_loop(self):
        window = windows[0]
        try:
            if target == 'none':
                qapp.processEvents()
                assert window.stack.currentWidget() is window.welcome
                assert window.welcome_paints.paints > 0
            else:
                wait_for(qapp, lambda: window.displayed_path == image)
                assert window.stack.currentWidget() is window.canvas
                assert window.welcome_paints.paints == 0
            assert window.isVisible()
            return 0
        finally:
            window.close()

    monkeypatch.setattr(QApplication, 'exec', run_event_loop)
    args = [] if target == 'none' else [str(tmp_path if target == 'folder' else image)]
    assert app_module.main(args) == 0
    assert before_show == [target != 'none']


def gate_worker(worker):
    """Keep decoding/scanning in flight while the GUI continues processing."""
    started, release = threading.Event(), threading.Event()
    original = worker.handler

    def delayed(payload, cancelled):
        started.set()
        while not release.wait(.01):
            if cancelled():
                return None
        return original(payload, cancelled)

    worker.handler = delayed
    return started, release


def test_slow_initial_decode_stays_visible_without_welcome(qapp, tmp_path):
    image = tmp_path / 'slow.png'
    Image.new('RGB', (80, 60), 'blue').save(image)
    window = ViewerWindow()
    counter = PaintCounter(window.welcome)
    started, release = gate_worker(window.loader)
    try:
        window.prepare_initial_path(image)
        window.show()
        qapp.processEvents()
        window.open_path(image)
        wait_for(qapp, lambda: started.is_set() and window.cancel_button.isVisible())
        assert window.isVisible()
        assert window.stack.currentWidget() is window.canvas
        assert counter.paints == 0
        assert 'Loading slow.png' in window.toast.text()
        release.set()
        wait_for(qapp, lambda: window.displayed_path == image)
        assert counter.paints == 0
        assert not window.cancel_button.isVisible()
    finally:
        release.set()
        window.close()


def test_cancelled_initial_decode_and_new_open_ignore_stale_results(qapp, tmp_path):
    first, second = tmp_path / 'first.png', tmp_path / 'second.png'
    Image.new('RGB', (80, 60), 'red').save(first)
    Image.new('RGB', (80, 60), 'green').save(second)
    window = ViewerWindow()
    started, release = gate_worker(window.loader)
    try:
        window.prepare_initial_path(first)
        window.show()
        window.open_path(first)
        wait_for(qapp, started.is_set)
        old_token = window._load_token
        window.cancel_load()
        assert window.isVisible()
        assert not window._pending_render
        assert not window.cancel_button.isVisible()
        assert window.dimension_label.text() == 'Cancelled'
        # Even an already queued failure from the old request is ignored.
        window._load_failed(old_token, 'obsolete error')
        assert window.dimension_label.text() == 'Cancelled'
        window.open_path(second)
        release.set()
        wait_for(qapp, lambda: window.displayed_path == second)
        assert window.stack.currentWidget() is window.canvas
    finally:
        release.set()
        window.close()


@pytest.mark.parametrize('outcome', ['empty', 'error', 'cancel'])
def test_folder_welcome_only_returns_at_terminal_no_image_state(qapp, tmp_path, outcome):
    window = ViewerWindow()
    counter = PaintCounter(window.welcome)
    if outcome == 'error':
        def fail_scan(payload, cancelled):
            raise PermissionError('unreadable test folder')
        window.scanner.handler = fail_scan
    started, release = gate_worker(window.scanner)
    try:
        window.prepare_initial_path(tmp_path)
        window.show()
        window.open_path(tmp_path)
        wait_for(qapp, started.is_set)
        assert window.isVisible()
        assert window.stack.currentWidget() is window.canvas
        assert counter.paints == 0
        assert window.cancel_button.isVisible()
        if outcome == 'cancel':
            token = window._scan_token
            window.cancel_load()
            window._scanned(token, (tmp_path, [tmp_path / 'obsolete.png'], None))
            assert window.path is None
            assert 'cancelled' in window.toast.text()
        else:
            release.set()
            wait_for(qapp, lambda: not window.cancel_button.isVisible())
            if outcome == 'empty':
                assert window.filename.text() == 'No images in this folder'
            else:
                assert 'Could not read folder' in window._error_dialog.text()
        assert window.stack.currentWidget() is window.welcome
        assert window.isVisible()
    finally:
        release.set()
        window.close()


@pytest.mark.parametrize('missing', [False, True])
def test_initial_bad_target_is_visible_and_can_recover(qapp, tmp_path, missing):
    bad, good = tmp_path / 'bad.png', tmp_path / 'good.png'
    if not missing:
        bad.write_bytes(b'invalid image')
    Image.new('RGB', (80, 60), 'red').save(good)
    window = ViewerWindow()
    try:
        window.prepare_initial_path(bad)
        window.show()
        window.open_path(bad)
        if missing:
            assert 'File not found' in window._error_dialog.text()
            assert window.stack.currentWidget() is window.welcome
        else:
            wait_for(qapp, lambda: window.dimension_label.text() == 'Unable to open')
            assert 'Could not open bad.png' in window._error_dialog.text()
            assert window.stack.currentWidget() is window.canvas
        assert window.isVisible()
        assert not window._pending_render
        assert not window.cancel_button.isVisible()
        # Open in this already running window uses the same transition.
        window.open_path(good)
        wait_for(qapp, lambda: window.displayed_path == good)
        assert window.stack.currentWidget() is window.canvas
    finally:
        window.close()


@pytest.mark.parametrize('scenario, expected', [('image', 0), ('none', 0), ('corrupt', 2), ('regression', 5)])
def test_real_smoke_event_loop_checks_painted_startup(tmp_path, scenario, expected):
    """The same bounded paint probe also runs inside the packaged executable."""
    import os
    import subprocess
    import sys

    image = tmp_path / '中文 startup.png'
    if scenario == 'corrupt':
        image.write_bytes(b'not an image')
    else:
        Image.new('RGB', (80, 60), 'red').save(image)
    script = (
        'import sys\n'
        'from luma import app\n'
        'if sys.argv[1] == "regression":\n'
        '    app.ViewerWindow.prepare_initial_path = lambda self, path: None\n'
        'args = ["--smoke-test"] + ([] if sys.argv[1] == "none" else [sys.argv[2]])\n'
        'raise SystemExit(app.main(args))\n'
    )
    result = subprocess.run(
        [sys.executable, '-c', script, scenario, str(image)],
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
        capture_output=True, text=True, errors='replace', timeout=15,
    )
    assert result.returncode == expected, result.stdout + result.stderr
