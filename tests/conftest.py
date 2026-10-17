import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from luma.app import STYLE

@pytest.fixture(scope='session')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    return app


@pytest.fixture(autouse=True)
def english_ui():
    """Existing English assertions must not depend on the host display locale."""
    from luma.i18n import set_language
    set_language('en')
    yield
    set_language('en')


@pytest.fixture(autouse=True)
def complete_qt_deferred_deletions(request):
    """Finish GUI-owned deleteLater work before the next test starts workers.

    processEvents() alone does not drain DeferredDelete without a running
    exec() loop. Embedded editors otherwise accumulate between tests and can
    be collected from a later worker thread instead of the GUI thread.
    """
    yield
    if 'qapp' in request.fixturenames:
        app = QApplication.instance()
        if app is not None:
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
