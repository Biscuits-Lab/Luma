import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
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
