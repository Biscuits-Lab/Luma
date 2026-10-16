from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QWidget
from PySide6.QtTest import QTest

from luma import update_startup
from luma.app import main


@pytest.mark.parametrize('arguments', [
    ['--update-startup-port', '12345'], ['--update-startup-nonce', 'a' * 64],
    ['--update-startup-port', '80', '--update-startup-nonce', 'a' * 64],
    ['--update-startup-port', '12345', '--update-startup-nonce', 'bad'],
    ['--smoke-test', '--update-startup-port', '12345', '--update-startup-nonce', 'a' * 64],
])
def test_invalid_startup_cli_rejected_before_window(arguments):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_ack_is_after_real_paint_and_only_once(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(update_startup, 'acknowledge_startup', lambda *args: calls.append(args))
    window = QWidget()
    controller = update_startup.StartupAcknowledgment(window, 12345, 'a' * 64)
    assert not calls
    window.show()
    QTest.qWait(50)
    qapp.processEvents()
    assert controller.sent
    assert calls == [(12345, 'a' * 64)]
    window.update()
    QTest.qWait(20)
    assert len(calls) == 1
    window.close()


def test_closed_window_cannot_acknowledge(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(update_startup, 'acknowledge_startup', lambda *args: calls.append(args))
    window = QWidget()
    controller = update_startup.StartupAcknowledgment(window, 12345, 'a' * 64)
    controller.eventFilter(window, QEvent(QEvent.Type.Paint))
    qapp.processEvents()
    assert not calls


def test_confirmed_registration_repair_has_exact_cli_route(monkeypatch):
    import sys
    from luma.__main__ import main as entry
    calls = []
    monkeypatch.setitem(sys.modules, 'luma.registration_repair', SimpleNamespace(main=lambda: calls.append(True) or 1))
    assert entry(['--shell-repair-registration']) == 1
    assert calls == [True]
